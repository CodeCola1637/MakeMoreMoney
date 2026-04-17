"""
P&L Analytics — 从 logs/orders.csv 聚合策略表现，输出：

1. compute_kelly_fraction() — 半凯利仓位上限（P2-14）
2. compute_strategy_winrates() — 按 signal_source 聚合胜率/平均盈亏，供权重反馈使用（P2-15）

仅依赖标准库，避免引入额外依赖。
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


@dataclass
class StrategyStats:
    source: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_win_pnl: float = 0.0
    total_loss_pnl: float = 0.0  # 取绝对值后累加
    
    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades > 0 else 0.0
    
    @property
    def avg_win(self) -> float:
        return self.total_win_pnl / self.wins if self.wins > 0 else 0.0
    
    @property
    def avg_loss(self) -> float:
        return self.total_loss_pnl / self.losses if self.losses > 0 else 0.0
    
    @property
    def payoff_ratio(self) -> float:
        """平均盈利 / 平均亏损"""
        if self.avg_loss <= 0:
            return 0.0
        return self.avg_win / self.avg_loss
    
    @property
    def kelly_fraction(self) -> float:
        """凯利公式: f* = (p*b - q) / b  其中 p=胜率, q=1-p, b=平均盈亏比"""
        b = self.payoff_ratio
        if b <= 0 or self.trades < 5:
            return 0.0
        p = self.win_rate
        q = 1 - p
        return max(0.0, (p * b - q) / b)
    
    @property
    def half_kelly_fraction(self) -> float:
        """半凯利（更稳健）"""
        return self.kelly_fraction / 2


def load_filled_trades(
    csv_path: str,
    lookback_days: int = 30,
    logger: Optional[logging.Logger] = None,
) -> List[Dict[str, str]]:
    """
    读取 orders.csv 中近 N 天已成交、且具有 realized_pnl 的卖出/平仓订单。
    
    Returns: list[dict]，每条 dict 至少包含 signal_source / realized_pnl / symbol
    """
    log = logger or logging.getLogger(__name__)
    if not os.path.exists(csv_path):
        log.debug(f"orders.csv 不存在: {csv_path}")
        return []
    
    cutoff = datetime.now() - timedelta(days=lookback_days)
    trades: List[Dict[str, str]] = []
    
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                status = (row.get("status") or "").strip()
                if status != "Filled":
                    continue
                
                pnl_raw = (row.get("realized_pnl") or "").strip()
                if not pnl_raw or pnl_raw.lower() == "none":
                    continue
                try:
                    pnl = float(pnl_raw)
                except (ValueError, TypeError):
                    continue
                if pnl == 0:
                    continue
                
                ts_raw = (row.get("filled_at") or row.get("created_at") or "").strip()
                if ts_raw:
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00").split("+")[0])
                        if ts < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass
                
                trades.append({
                    "symbol": row.get("symbol", ""),
                    "signal_source": (row.get("signal_source") or "unknown").strip() or "unknown",
                    "realized_pnl": pnl,
                    "side": row.get("side", ""),
                })
    except Exception as e:
        log.warning(f"读取 orders.csv 失败: {e}")
    
    return trades


def compute_strategy_winrates(
    csv_path: str,
    lookback_days: int = 30,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, StrategyStats]:
    """按 signal_source 聚合，返回 {source: StrategyStats}"""
    trades = load_filled_trades(csv_path, lookback_days, logger)
    bucket: Dict[str, StrategyStats] = {}
    for t in trades:
        src = t["signal_source"] or "unknown"
        s = bucket.setdefault(src, StrategyStats(source=src))
        s.trades += 1
        pnl = float(t["realized_pnl"])
        if pnl > 0:
            s.wins += 1
            s.total_win_pnl += pnl
        else:
            s.losses += 1
            s.total_loss_pnl += abs(pnl)
    return bucket


def compute_global_kelly(
    csv_path: str,
    lookback_days: int = 30,
    min_trades: int = 10,
    logger: Optional[logging.Logger] = None,
) -> Tuple[float, StrategyStats]:
    """
    计算全局半凯利仓位比例。
    
    Returns: (half_kelly_fraction, aggregate_stats)
        half_kelly_fraction = 0.0 → 数据不足或负期望，建议保持默认 position_pct
    """
    trades = load_filled_trades(csv_path, lookback_days, logger)
    agg = StrategyStats(source="__global__")
    for t in trades:
        agg.trades += 1
        pnl = float(t["realized_pnl"])
        if pnl > 0:
            agg.wins += 1
            agg.total_win_pnl += pnl
        else:
            agg.losses += 1
            agg.total_loss_pnl += abs(pnl)
    
    if agg.trades < min_trades:
        return 0.0, agg
    return agg.half_kelly_fraction, agg
