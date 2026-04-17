"""Shared context dataclass for all trading tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, List, Optional, Set

logger = logging.getLogger("tasks")


@dataclass
class TradingContext:
    """Holds every component that task functions need, replacing closure captures."""

    config: Any  # ConfigLoader
    symbols: List[str]
    realtime_mgr: Any  # RealtimeDataManager
    order_mgr: Any  # OrderManager
    portfolio_mgr: Any  # PortfolioManager
    profit_stop_mgr: Any  # ProfitStopManager
    signal_gen: Any  # StrategyEnsemble | SignalGenerator
    on_signal: Callable[..., Coroutine]
    task_manager: Any  # TaskManager

    stock_discovery: Optional[Any] = None
    institutional_tracker: Optional[Any] = None
    sec_strategy: Optional[Any] = None
    volume_detector: Optional[Any] = None
    volume_strategy: Optional[Any] = None
    ccass_tracker: Optional[Any] = None
    ccass_strategy: Optional[Any] = None
    ensemble_enabled: bool = True

    _sec_discovered: Set[str] = field(default_factory=set)

    async def add_symbol(self, symbol: str) -> bool:
        """动态添加标的：订阅行情、构建基线、加入关注列表。

        Returns True if the symbol was newly added, False if already present.
        """
        if symbol in self.symbols:
            return False

        try:
            from longport.openapi import SubType
            await self.realtime_mgr.subscribe([symbol], [SubType.Quote])
            logger.info(f"🔔 已订阅 {symbol} 实时行情")
        except Exception as e:
            logger.warning(f"订阅 {symbol} 行情失败: {e}")
            return False

        self.symbols.append(symbol)

        if self.volume_detector and getattr(self.volume_detector, "_started", False):
            try:
                await self.volume_detector._build_baseline(symbol)
                logger.info(f"📊 已构建 {symbol} 成交量基线")
            except Exception as e:
                logger.warning(f"构建 {symbol} 成交量基线失败: {e}")

        logger.info(f"✅ 标的 {symbol} 已动态加入关注列表 (当前共 {len(self.symbols)} 只)")
        return True
