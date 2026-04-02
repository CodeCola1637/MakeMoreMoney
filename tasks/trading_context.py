"""Shared context dataclass for all trading tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, List, Optional

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
    ensemble_enabled: bool = True
