#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
异常成交量策略适配器

将 VolumeAnomalyDetector 的检测结果适配为 StrategyEnsemble 可用的策略接口。
信号有效期 30 分钟（成交量异常是实时信号）。
多种异常类型叠加时置信度复合增强。
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Dict, List, Optional

from strategy.signals import Signal, SignalType
from utils import setup_logger


class VolumeStrategy:
    """异常成交量策略 — 作为 StrategyEnsemble 的一个投票者"""

    DEFAULT_EXPIRY_MINUTES = 30

    def __init__(self, config, logger: logging.Logger = None):
        self.config = config
        self.logger = logger or setup_logger(
            "volume_strategy",
            config.get("logging.level", "INFO"),
            config.get("logging.file"),
        )
        self.expiry_minutes = config.get(
            "volume_anomaly.signal_expiry_minutes", self.DEFAULT_EXPIRY_MINUTES
        )
        # symbol -> List[{signal: VolumeSignal, detected_at: datetime}]
        self._cache: Dict[str, list] = {}
        self._lock = threading.Lock()

        # 即时评估回调（由 main.py 注入）
        self._on_anomaly_callback: Optional[Callable] = None

        self.logger.info(
            f"VolumeStrategy 初始化: 信号有效期={self.expiry_minutes}min"
        )

    # ----------------------------------------------------------
    # 回调注册（用于事件驱动即时评估）
    # ----------------------------------------------------------

    def set_anomaly_callback(self, callback: Callable):
        """注册异常触发时的即时评估回调

        callback 签名: async def callback(symbol: str) -> None
        """
        self._on_anomaly_callback = callback

    # ----------------------------------------------------------
    # 缓存管理（由 volume_anomaly_task 调用）
    # ----------------------------------------------------------

    def update_signals(self, vol_signals: list):
        """接收 VolumeAnomalyDetector.check_and_generate_signals() 的结果"""
        now = datetime.now()
        triggered_symbols = []

        with self._lock:
            for vs in vol_signals:
                if vs.symbol not in self._cache:
                    self._cache[vs.symbol] = []
                self._cache[vs.symbol].append({
                    "signal": vs,
                    "detected_at": now,
                })
                triggered_symbols.append(vs.symbol)
            self._cleanup_expired(now)

        if triggered_symbols:
            self.logger.info(
                f"Volume 缓存更新: {len(vol_signals)} 个信号, "
                f"symbols={triggered_symbols}"
            )

        return triggered_symbols

    def _cleanup_expired(self, now: datetime):
        cutoff = now - timedelta(minutes=self.expiry_minutes)
        for sym in list(self._cache.keys()):
            self._cache[sym] = [
                e for e in self._cache[sym] if e["detected_at"] >= cutoff
            ]
            if not self._cache[sym]:
                del self._cache[sym]

    # ----------------------------------------------------------
    # StrategyEnsemble 接口
    # ----------------------------------------------------------

    async def generate_signal(
        self, symbol: str, data: Dict[str, Any]
    ) -> Optional[Signal]:
        """
        被 StrategyEnsemble._get_strategy_signal 调用。
        聚合近 30 分钟内该 symbol 的所有异常信号，复合计算置信度和方向。
        无数据时返回 None（弃权，不参与投票）。
        """
        price = data.get("last_done", 0)
        now = datetime.now()
        cutoff = now - timedelta(minutes=self.expiry_minutes)

        with self._lock:
            entries = self._cache.get(symbol, [])
            recent = [e for e in entries if e["detected_at"] >= cutoff]

        if not recent:
            self.logger.debug(f"Volume 无 {symbol} 异常数据，弃权")
            return None

        buy_score = 0.0
        sell_score = 0.0
        buy_count = 0
        sell_count = 0
        anomaly_types = set()
        reasons = []

        for entry in recent:
            vs = entry["signal"]
            anomaly_types.update(
                a.anomaly_type.value for a in vs.anomalies
            )
            reasons.append(vs.reason)

            if vs.signal_type == "BUY":
                buy_score += vs.confidence
                buy_count += 1
            elif vs.signal_type == "SELL":
                sell_score += vs.confidence
                sell_count += 1

        total_directional = buy_count + sell_count
        if total_directional == 0:
            return None

        # 多空分歧检测：买卖信号并存且分数接近时弃权
        dominant_count = max(buy_count, sell_count)
        consistency = dominant_count / total_directional
        if buy_count > 0 and sell_count > 0:
            score_ratio = min(buy_score, sell_score) / max(buy_score, sell_score) if max(buy_score, sell_score) > 0 else 1
            if score_ratio > 0.4:
                self.logger.info(
                    f"Volume {symbol} 多空分歧: buy={buy_count}({buy_score:.3f}) "
                    f"vs sell={sell_count}({sell_score:.3f}), 分数比={score_ratio:.2f}, 弃权"
                )
                return None
        if consistency < 0.7 and total_directional >= 3:
            self.logger.info(
                f"Volume {symbol} 方向不一致(buy={buy_count}, sell={sell_count}, "
                f"一致性={consistency:.0%})，弃权"
            )
            return None

        type_bonus = 1.0 + 0.15 * max(0, len(anomaly_types) - 1)

        if buy_score >= sell_score and buy_score > 0:
            sig_type = SignalType.BUY
            raw_confidence = buy_score / len(recent)
        elif sell_score > 0:
            sig_type = SignalType.SELL
            raw_confidence = sell_score / len(recent)
        else:
            return None

        confidence = min(0.95, raw_confidence * type_bonus)
        # 方向一致性越高，置信度越高
        confidence *= consistency

        return Signal(
            symbol=symbol,
            signal_type=sig_type,
            price=price,
            confidence=confidence,
            quantity=1,
            strategy_name="volume_anomaly",
            extra_data={
                "anomaly_types": list(anomaly_types),
                "anomaly_count": len(recent),
                "type_bonus": round(type_bonus, 2),
                "buy_score": round(buy_score, 3),
                "sell_score": round(sell_score, 3),
                "reasons": reasons[:3],
            },
        )

    # ----------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------

    def has_active_signals(self, symbol: str) -> bool:
        now = datetime.now()
        cutoff = now - timedelta(minutes=self.expiry_minutes)
        with self._lock:
            entries = self._cache.get(symbol, [])
            return any(e["detected_at"] >= cutoff for e in entries)

    def get_active_symbols(self) -> List[str]:
        now = datetime.now()
        cutoff = now - timedelta(minutes=self.expiry_minutes)
        with self._lock:
            return [
                sym for sym, entries in self._cache.items()
                if any(e["detected_at"] >= cutoff for e in entries)
            ]

    def get_summary(self) -> str:
        active = self.get_active_symbols()
        with self._lock:
            total = sum(len(v) for v in self._cache.values())
        return (
            f"Volume Strategy: {len(active)} active symbols, "
            f"{total} cached signals"
        )
