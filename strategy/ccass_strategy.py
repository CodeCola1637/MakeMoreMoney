#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CCASS 持仓策略适配器

将 CCASTracker 的扫描结果适配为 StrategyEnsemble 可用的策略接口。
仅对港股(.HK)生效，美股(.US)自动弃权。
信号有效期 24 小时（CCASS 数据每日更新一次）。
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from strategy.signals import Signal, SignalType
from utils import setup_logger


class CCASStrategy:
    """CCASS 持仓变化策略 — 作为 StrategyEnsemble 的一个投票者（仅港股）"""

    DEFAULT_EXPIRY_HOURS = 24

    def __init__(self, config, logger: logging.Logger = None):
        self.config = config
        self.logger = logger or setup_logger(
            "ccass_strategy",
            config.get("logging.level", "INFO"),
            config.get("logging.file"),
        )
        self.expiry_hours = config.get(
            "ccass.signal_expiry_hours", self.DEFAULT_EXPIRY_HOURS
        )
        # symbol -> {signal: CCASSignal, updated_at: datetime}
        self._cache: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.logger.info(
            f"CCASStrategy 初始化: 信号有效期={self.expiry_hours}h (仅港股)"
        )

    # ----------------------------------------------------------
    # 缓存管理（由 ccass_tracking_task 调用）
    # ----------------------------------------------------------

    def update_signals(self, signals: list):
        """接收 CCASTracker.scan_symbols() 的结果并缓存"""
        now = datetime.now()
        with self._lock:
            for sig in signals:
                self._cache[sig.symbol] = {
                    "signal": sig,
                    "updated_at": now,
                }
            self._cleanup_expired(now)
        if signals:
            self.logger.info(
                f"CCASS 缓存更新: {len(signals)} 个信号, "
                f"symbols={[s.symbol for s in signals]}"
            )

    def _cleanup_expired(self, now: datetime):
        cutoff = now - timedelta(hours=self.expiry_hours)
        expired = [k for k, v in self._cache.items() if v["updated_at"] < cutoff]
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
        仅对港股(.HK)有效；美股直接返回 None（弃权）。
        """
        if not symbol.endswith(".HK"):
            return None

        price = data.get("last_done", 0)
        now = datetime.now()
        cutoff = now - timedelta(hours=self.expiry_hours)

        with self._lock:
            entry = self._cache.get(symbol)

        if entry and entry["updated_at"] >= cutoff:
            ccas_sig = entry["signal"]
            if ccas_sig.signal_type == "BUY":
                sig_type = SignalType.BUY
            elif ccas_sig.signal_type == "SELL":
                sig_type = SignalType.SELL
            else:
                return None

            return Signal(
                symbol=symbol,
                signal_type=sig_type,
                price=price,
                confidence=ccas_sig.confidence,
                quantity=1,
                strategy_name="ccass",
                extra_data={
                    "reason": ccas_sig.reason,
                    "net_change_pct": ccas_sig.net_change_pct,
                    "net_change_shares": ccas_sig.net_change_shares,
                    "top_movers": ccas_sig.top_movers[:3],
                },
            )

        self.logger.debug(f"CCASS 无 {symbol} 数据，弃权")
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
        return f"CCASS Strategy: {n} cached signals, symbols={symbols}"
