#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CCASS (中央結算系統) 持仓追踪模块

通过 HKEX 公开数据跟踪港股在 CCASS 中的参与者持仓变动，
检测大型券商/机构的显著增减仓行为，生成交易信号。

数据来源: HKEX Shareholding Disclosure
更新频率: 每个交易日收盘后 (约 T+1 06:00 HKT)
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from utils import setup_logger


# ============================================================
# 数据类
# ============================================================

@dataclass
class CCASParticipantHolding:
    """单个 CCASS 参与者持仓"""
    participant_id: str
    participant_name: str
    shareholding: int
    pct_of_total: float
    date: str


@dataclass
class CCASSDailySnapshot:
    """单只股票某日的 CCASS 快照"""
    stock_code: str
    date: str
    total_issued: int
    total_ccass: int
    ccass_pct: float
    top_holders: List[CCASParticipantHolding] = field(default_factory=list)


@dataclass
class CCASSignal:
    """CCASS 信号"""
    symbol: str
    signal_type: str  # BUY, SELL
    confidence: float
    reason: str = ""
    net_change_shares: int = 0
    net_change_pct: float = 0.0
    top_movers: List[Dict] = field(default_factory=list)


# ============================================================
# 已知大型参与者 (券商/托管行) — 代表机构资金流向
# ============================================================

MAJOR_PARTICIPANTS = {
    "HKSCC NOMINEES LIMITED": "港股通(沪/深)",
    "CITIBANK N.A.": "花旗 (外资托管)",
    "HSBC-HONGKONG": "汇丰 (本地大行)",
    "JPMORGAN CHASE BANK": "摩根大通 (外资)",
    "STANDARD CHARTERED BANK": "渣打 (外资)",
    "BNP PARIBAS": "法巴 (外资)",
    "CITICORP INTERNATIONAL": "中信国际",
    "UBS AG": "瑞银",
    "GOLDMAN SACHS": "高盛",
    "MORGAN STANLEY": "摩根士丹利",
    "BOCI SECURITIES": "中银国际",
    "CHINA SECURITIES": "中信证券",
    "FUTU SECURITIES": "富途证券",
    "INTERACTIVE BROKERS": "盈透证券",
}


class CCASTracker:
    """CCASS 持仓追踪器"""

    CACHE_DIR = "data_cache/ccass"
    HKEX_URL = "https://www3.hkexnews.hk/sdw/search/searchsdw.aspx"

    def __init__(self, config, logger: logging.Logger = None):
        self.config = config
        self.logger = logger or setup_logger(
            "ccass_tracker",
            config.get("logging.level", "INFO"),
            config.get("logging.file"),
        )
        self.enabled = config.get("ccass.enable", True)
        self.min_change_pct = config.get("ccass.min_change_pct", 0.5)
        self.lookback_days = config.get("ccass.lookback_days", 5)
        self.top_n = config.get("ccass.top_n", 10)

        os.makedirs(self.CACHE_DIR, exist_ok=True)

        self._snapshots: Dict[str, List[CCASSDailySnapshot]] = {}
        self._last_scan: Optional[datetime] = None
        self._load_cache()

        self.logger.info(
            f"CCASS 追踪器初始化: min_change={self.min_change_pct}%, "
            f"lookback={self.lookback_days}天, top_n={self.top_n}"
        )

    # ----------------------------------------------------------
    # 数据获取
    # ----------------------------------------------------------

    def _hk_code_from_symbol(self, symbol: str) -> str:
        """700.HK -> 00700"""
        code = symbol.replace(".HK", "").replace(".hk", "")
        return code.zfill(5)

    def _fetch_ccass_page(self, stock_code: str, date_str: str) -> Optional[str]:
        """
        从 HKEX 获取 CCASS 持仓页面 HTML。
        stock_code: 5位代码如 '00700'
        date_str: 格式 'YYYY/MM/DD'
        """
        try:
            get_headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": self.HKEX_URL,
            }

            # Step 1: GET the page to retrieve viewstate tokens (不带 Content-Type)
            req = Request(self.HKEX_URL, headers=get_headers)
            with urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8")

            viewstate = self._extract_hidden(html, "__VIEWSTATE")
            viewstate_gen = self._extract_hidden(html, "__VIEWSTATEGENERATOR")
            event_validation = self._extract_hidden(html, "__EVENTVALIDATION")

            if not viewstate:
                self.logger.warning("无法获取 HKEX CCASS viewstate")
                return None

            # Step 2: POST with search parameters
            from urllib.parse import urlencode
            post_data = urlencode({
                "__VIEWSTATE": viewstate,
                "__VIEWSTATEGENERATOR": viewstate_gen or "",
                "__EVENTVALIDATION": event_validation or "",
                "today": date_str.replace("/", ""),
                "txtShareholdingDate": date_str,
                "txtStockCode": stock_code,
                "btnSearch": "搜尋",
                "sortBy": "shareholding",
                "alertMsg": "",
            }).encode("utf-8")

            post_headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "Referer": self.HKEX_URL,
                "Content-Type": "application/x-www-form-urlencoded",
            }
            req2 = Request(self.HKEX_URL, data=post_data, headers=post_headers)
            with urlopen(req2, timeout=15) as resp2:
                return resp2.read().decode("utf-8")

        except (URLError, HTTPError, Exception) as e:
            self.logger.warning(f"CCASS 数据获取失败 ({stock_code} @ {date_str}): {e}")
            return None

    @staticmethod
    def _extract_hidden(html: str, field_name: str) -> Optional[str]:
        pattern = rf'id="{field_name}"\s+value="([^"]*)"'
        m = re.search(pattern, html)
        return m.group(1) if m else None

    def _parse_ccass_html(self, html: str, stock_code: str, date_str: str) -> Optional[CCASSDailySnapshot]:
        """解析 CCASS 搜索结果 HTML"""
        try:
            participants = []

            tag_strip = re.compile(r'<[^>]+>')
            td_pattern = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL | re.IGNORECASE)
            tr_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
            pid_pattern = re.compile(r'[A-Z]\d{5}')

            for row_html in tr_pattern.findall(html):
                cells = td_pattern.findall(row_html)
                if len(cells) < 4:
                    continue

                clean = [tag_strip.sub("", c).strip() for c in cells]

                # HKEX 格式: td[0]=Participant ID, td[1]=Name, td[2]=Address, td[3]=Shareholding, td[4]=%
                # 提取各字段中的实际数据（跳过标签文本）
                pid_text = clean[0]
                pid_match = pid_pattern.search(pid_text)
                if not pid_match:
                    continue
                pid = pid_match.group(0)

                name_lines = [l.strip() for l in clean[1].split("\n") if l.strip()]
                pname = name_lines[-1] if len(name_lines) > 1 else name_lines[0] if name_lines else ""
                pname = pname.rstrip("*").strip()

                shares_text = clean[3] if len(clean) > 3 else ""
                shares_digits = re.sub(r'[^\d]', '', shares_text.split('\n')[-1].strip())

                pct_text = clean[4] if len(clean) > 4 else ""
                pct_match = re.search(r'([\d.]+)%', pct_text)

                if not shares_digits:
                    continue

                try:
                    shares = int(shares_digits)
                except ValueError:
                    shares = 0
                pct = float(pct_match.group(1)) if pct_match else 0.0

                if shares > 0 and pname:
                    participants.append(CCASParticipantHolding(
                        participant_id=pid,
                        participant_name=pname,
                        shareholding=shares,
                        pct_of_total=pct,
                        date=date_str,
                    ))

            if not participants:
                self.logger.debug(f"CCASS 未解析到持仓数据: {stock_code} @ {date_str}")
                return None

            participants.sort(key=lambda x: x.shareholding, reverse=True)
            total_ccass = sum(p.shareholding for p in participants)

            snapshot = CCASSDailySnapshot(
                stock_code=stock_code,
                date=date_str,
                total_issued=0,
                total_ccass=total_ccass,
                ccass_pct=0.0,
                top_holders=participants[:self.top_n],
            )
            self.logger.debug(
                f"CCASS 解析成功: {stock_code} @ {date_str}, "
                f"{len(participants)} 参与者, 总持仓={total_ccass:,}"
            )
            return snapshot

        except Exception as e:
            self.logger.error(f"CCASS HTML 解析失败: {e}")
            return None

    # ----------------------------------------------------------
    # 扫描与信号生成
    # ----------------------------------------------------------

    async def scan_symbols(self, symbols: List[str]) -> List[CCASSignal]:
        """扫描指定港股的 CCASS 持仓变化"""
        hk_symbols = [s for s in symbols if s.endswith(".HK")]
        if not hk_symbols:
            return []

        signals = []
        now = datetime.now()

        for symbol in hk_symbols:
            try:
                signal = await self._scan_single(symbol, now)
                if signal:
                    signals.append(signal)
                await asyncio.sleep(2)
            except Exception as e:
                self.logger.error(f"CCASS 扫描 {symbol} 失败: {e}")

        self._last_scan = now
        self._save_cache()

        if signals:
            self.logger.info(
                f"CCASS 扫描完成: {len(hk_symbols)} 只港股, "
                f"产生 {len(signals)} 个信号"
            )
        return signals

    async def _scan_single(self, symbol: str, now: datetime) -> Optional[CCASSignal]:
        """扫描单只股票的 CCASS 变化"""
        code = self._hk_code_from_symbol(symbol)

        snapshots = []
        for days_ago in range(self.lookback_days + 1):
            d = now - timedelta(days=days_ago)
            if d.weekday() >= 5:
                continue
            date_str = d.strftime("%Y/%m/%d")

            cached = self._get_cached_snapshot(symbol, date_str)
            if cached:
                snapshots.append(cached)
                continue

            html = await asyncio.to_thread(self._fetch_ccass_page, code, date_str)
            if html:
                snap = self._parse_ccass_html(html, code, date_str)
                if snap:
                    snap.stock_code = symbol
                    snapshots.append(snap)
                    self._cache_snapshot(symbol, snap)

            await asyncio.sleep(1)

        if len(snapshots) < 2:
            return None

        snapshots.sort(key=lambda s: s.date, reverse=True)
        return self._analyze_changes(symbol, snapshots)

    def _analyze_changes(self, symbol: str, snapshots: List[CCASSDailySnapshot]) -> Optional[CCASSignal]:
        """分析持仓变化，生成信号"""
        latest = snapshots[0]
        prev = snapshots[1]

        latest_map = {h.participant_name.upper(): h for h in latest.top_holders}
        prev_map = {h.participant_name.upper(): h for h in prev.top_holders}

        all_names = set(latest_map.keys()) | set(prev_map.keys())

        net_change = 0
        movers = []

        for name in all_names:
            curr_shares = latest_map[name].shareholding if name in latest_map else 0
            prev_shares = prev_map[name].shareholding if name in prev_map else 0
            change = curr_shares - prev_shares

            if change == 0:
                continue

            is_major = any(k.upper() in name for k in MAJOR_PARTICIPANTS.keys())
            label = ""
            for k, v in MAJOR_PARTICIPANTS.items():
                if k.upper() in name:
                    label = v
                    break

            change_pct = abs(change) / prev_shares * 100 if prev_shares > 0 else 100.0

            if is_major or change_pct >= self.min_change_pct:
                movers.append({
                    "name": label or name[:30],
                    "change": change,
                    "change_pct": round(change_pct, 2),
                    "curr_shares": curr_shares,
                    "direction": "增持" if change > 0 else "减持",
                })
                weight = 2.0 if is_major else 1.0
                net_change += change * weight

        if not movers:
            return None

        total_base = prev.total_ccass if prev.total_ccass > 0 else 1
        net_change_pct = net_change / total_base * 100

        if abs(net_change_pct) < self.min_change_pct:
            self.logger.debug(
                f"CCASS {symbol}: 净变化 {net_change_pct:.2f}% < 阈值 {self.min_change_pct}%, 忽略"
            )
            return None

        if len(snapshots) >= 3:
            trend_direction = 0
            for i in range(len(snapshots) - 1):
                curr_total = snapshots[i].total_ccass
                prev_total = snapshots[i + 1].total_ccass
                if curr_total > prev_total:
                    trend_direction += 1
                elif curr_total < prev_total:
                    trend_direction -= 1
            trend_consistent = (trend_direction > 0 and net_change > 0) or \
                               (trend_direction < 0 and net_change < 0)
        else:
            trend_consistent = False

        base_confidence = min(0.4, abs(net_change_pct) / 5.0 * 0.4)
        if trend_consistent:
            base_confidence = min(0.7, base_confidence + 0.15)
        major_movers = [m for m in movers if any(k.upper() in m["name"].upper() or m["name"] in MAJOR_PARTICIPANTS.values() for k in MAJOR_PARTICIPANTS.keys())]
        if len(major_movers) >= 2:
            base_confidence = min(0.8, base_confidence + 0.1)

        signal_type = "BUY" if net_change > 0 else "SELL"

        movers.sort(key=lambda m: abs(m["change"]), reverse=True)
        top_movers = movers[:5]
        reason_parts = [
            f"CCASS {latest.date}: 净{'增' if net_change > 0 else '减'}仓 {abs(net_change_pct):.2f}%",
        ]
        for m in top_movers[:3]:
            reason_parts.append(f"{m['name']} {m['direction']}{abs(m['change']):,}股({m['change_pct']:.1f}%)")

        return CCASSignal(
            symbol=symbol,
            signal_type=signal_type,
            confidence=round(base_confidence, 3),
            reason="; ".join(reason_parts),
            net_change_shares=net_change,
            net_change_pct=round(net_change_pct, 4),
            top_movers=top_movers,
        )

    # ----------------------------------------------------------
    # 缓存
    # ----------------------------------------------------------

    def _cache_key(self, symbol: str, date_str: str) -> str:
        return f"{symbol}_{date_str.replace('/', '')}"

    def _get_cached_snapshot(self, symbol: str, date_str: str) -> Optional[CCASSDailySnapshot]:
        key = self._cache_key(symbol, date_str)
        snaps = self._snapshots.get(key)
        if snaps and isinstance(snaps, CCASSDailySnapshot):
            return snaps
        return None

    def _cache_snapshot(self, symbol: str, snap: CCASSDailySnapshot):
        key = self._cache_key(symbol, snap.date)
        self._snapshots[key] = snap

    def _save_cache(self):
        try:
            cache_file = os.path.join(self.CACHE_DIR, "ccass_cache.json")
            data = {}
            for k, v in self._snapshots.items():
                if isinstance(v, CCASSDailySnapshot):
                    data[k] = {
                        "stock_code": v.stock_code,
                        "date": v.date,
                        "total_issued": v.total_issued,
                        "total_ccass": v.total_ccass,
                        "ccass_pct": v.ccass_pct,
                        "top_holders": [
                            {
                                "participant_id": h.participant_id,
                                "participant_name": h.participant_name,
                                "shareholding": h.shareholding,
                                "pct_of_total": h.pct_of_total,
                                "date": h.date,
                            }
                            for h in v.top_holders
                        ],
                    }
            tmp = cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cache_file)
        except Exception as e:
            self.logger.warning(f"CCASS 缓存保存失败: {e}")

    def _load_cache(self):
        try:
            cache_file = os.path.join(self.CACHE_DIR, "ccass_cache.json")
            if not os.path.exists(cache_file):
                return
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                holders = [
                    CCASParticipantHolding(**h) for h in v.get("top_holders", [])
                ]
                self._snapshots[k] = CCASSDailySnapshot(
                    stock_code=v["stock_code"],
                    date=v["date"],
                    total_issued=v.get("total_issued", 0),
                    total_ccass=v.get("total_ccass", 0),
                    ccass_pct=v.get("ccass_pct", 0),
                    top_holders=holders,
                )
            self.logger.info(f"CCASS 缓存加载: {len(self._snapshots)} 条记录")
        except Exception as e:
            self.logger.warning(f"CCASS 缓存加载失败: {e}")

    # ----------------------------------------------------------
    # 摘要
    # ----------------------------------------------------------

    def get_summary(self) -> str:
        lines = ["📊 CCASS 持仓追踪器摘要:"]
        lines.append(f"  缓存记录: {len(self._snapshots)} 条")
        if self._last_scan:
            lines.append(f"  上次扫描: {self._last_scan.strftime('%Y-%m-%d %H:%M')}")
        return "\n".join(lines)
