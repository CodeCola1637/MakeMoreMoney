#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SEC 披露策略适配器

将 InstitutionalTracker 的扫描结果适配为 StrategyEnsemble 可用的策略接口。
信号有效期 48 小时（SEC Form 4 需在 2 个工作日内披露）。
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from strategy.signals import Signal, SignalType
from utils import setup_logger


class SECStrategy:
    """SEC 披露策略 — 作为 StrategyEnsemble 的一个投票者"""

    DEFAULT_EXPIRY_HOURS = 48

    def __init__(self, config, logger: logging.Logger = None):
        self.config = config
        self.logger = logger or setup_logger(
            "sec_strategy",
            config.get("logging.level", "INFO"),
            config.get("logging.file"),
        )
        self.expiry_hours = config.get(
            "institutional.signal_expiry_hours", self.DEFAULT_EXPIRY_HOURS
        )
        # symbol -> {signal: InstitutionalSignal, updated_at: datetime}
        self._cache: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.logger.info(
            f"SECStrategy 初始化: 信号有效期={self.expiry_hours}h"
        )

    # ----------------------------------------------------------
    # 缓存管理（由 institutional_tracking_task 调用）
    # ----------------------------------------------------------

    def update_signals(self, signals: list):
        """接收 InstitutionalTracker.run_scan_cycle() 的结果并缓存"""
        now = datetime.now()
        with self._lock:
            for sig in signals:
                filing_dt = self._parse_filing_date(getattr(sig, 'filing_date', ''))
                self._cache[sig.symbol] = {
                    "signal": sig,
                    "filing_at": filing_dt or now,
                    "updated_at": now,
                }
            self._cleanup_expired(now)
        if signals:
            self.logger.info(
                f"SEC 缓存更新: {len(signals)} 个信号, "
                f"symbols={[s.symbol for s in signals]}"
            )

    @staticmethod
    def _parse_filing_date(filing_date_str: str) -> Optional[datetime]:
        """将 filing_date 字符串解析为 datetime"""
        if not filing_date_str:
            return None
        for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%m/%d/%Y'):
            try:
                return datetime.strptime(filing_date_str, fmt)
            except ValueError:
                continue
        return None

    def _cleanup_expired(self, now: datetime):
        cutoff = now - timedelta(hours=self.expiry_hours)
        expired = [k for k, v in self._cache.items() if v["filing_at"] < cutoff]
        for k in expired:
            del self._cache[k]

    # ----------------------------------------------------------
    # StrategyEnsemble 接口
    # ----------------------------------------------------------

    async def generate_signal(
        self, symbol: str, data: Dict[str, Any]
    ) -> Optional[Signal]:
        """
        被 StrategyEnsemble._get_strategy_signal 调用。
        有数据时返回 BUY/SELL/HOLD Signal；无数据时返回 None（弃权）。
        """
        price = data.get("last_done", 0)
        now = datetime.now()
        cutoff = now - timedelta(hours=self.expiry_hours)

        with self._lock:
            entry = self._cache.get(symbol)

        if entry and entry["filing_at"] >= cutoff:
            inst_sig = entry["signal"]
            if inst_sig.signal_type == "BUY":
                sig_type = SignalType.BUY
            elif inst_sig.signal_type == "SELL":
                sig_type = SignalType.SELL
            else:
                sig_type = SignalType.HOLD

            return Signal(
                symbol=symbol,
                signal_type=sig_type,
                price=price,
                confidence=inst_sig.confidence,
                quantity=1,
                strategy_name="sec",
                extra_data={
                    "reason": inst_sig.reason,
                    "sources": inst_sig.sources[:3],
                    "institutional_score": inst_sig.institutional_score,
                    "insider_buy": inst_sig.insider_buy_count,
                    "insider_sell": inst_sig.insider_sell_count,
                    "institutions_buying": inst_sig.institutions_buying,
                    "institutions_selling": inst_sig.institutions_selling,
                },
            )

        # 无相关数据 → 弃权，不参与投票
        self.logger.debug(f"SEC 无 {symbol} 数据，弃权")
        return None

    # ----------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------

    def get_cached_symbols(self) -> List[str]:
        with self._lock:
            return list(self._cache.keys())

    def get_summary(self) -> str:
        with self._lock:
            n = len(self._cache)
            symbols = list(self._cache.keys())[:5]
        return f"SEC Strategy: {n} cached signals, symbols={symbols}"
