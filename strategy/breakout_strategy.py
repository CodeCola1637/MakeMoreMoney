#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
通道突破策略 (Channel Breakout Strategy)

检测价格是否突破近 N 日最高/最低价，为 StrategyEnsemble 提供方向确认。
作为辅助策略：不能独立触发交易，仅在 Volume Anomaly / SEC 触发时投票。
"""

import logging
from typing import Any, Dict, Optional

import numpy as np

from strategy.signals import Signal, SignalType
from utils import setup_logger


class BreakoutStrategy:
    """通道突破策略 — 作为 StrategyEnsemble 的辅助投票者"""

    def __init__(self, config, historical_loader, logger: logging.Logger = None):
        self.config = config
        self.historical_loader = historical_loader
        self.logger = logger or setup_logger(
            "breakout_strategy",
            config.get("logging.level", "INFO"),
            config.get("logging.file"),
        )

        self.lookback = config.get("strategy.breakout.lookback_days", 20)
        self.atr_period = config.get("strategy.breakout.atr_period", 14)
        self.breakout_margin = config.get("strategy.breakout.margin_pct", 0.002)

        self.logger.info(
            f"BreakoutStrategy 初始化: 回看={self.lookback}天, "
            f"ATR周期={self.atr_period}, 突破余量={self.breakout_margin:.1%}"
        )

    async def generate_signal(
        self, symbol: str, data: Dict[str, Any]
    ) -> Optional[Signal]:
        """StrategyEnsemble 调用入口。

        比较当前价格与近 N 日通道上下轨，返回方向投票。
        数据不足时返回 None（弃权）。
        """
        price = data.get("last_done", 0)
        if price <= 0:
            return None

        df = await self._get_historical(symbol)
        if df is None or len(df) < self.lookback:
            self.logger.debug(f"Breakout {symbol}: 历史数据不足，弃权")
            return None

        recent = df.tail(self.lookback)
        channel_high = float(recent["high"].max())
        channel_low = float(recent["low"].min())
        channel_mid = (channel_high + channel_low) / 2
        channel_width = channel_high - channel_low

        if channel_width <= 0:
            return None

        atr = self._calc_atr(df)

        upper_threshold = channel_high * (1 - self.breakout_margin)
        lower_threshold = channel_low * (1 + self.breakout_margin)

        position_in_channel = (price - channel_low) / channel_width

        if price >= upper_threshold:
            sig_type = SignalType.BUY
            raw_conf = min(0.9, 0.4 + (price - upper_threshold) / atr * 0.2) if atr > 0 else 0.5
            reason = (
                f"突破{self.lookback}日高点 {channel_high:.2f} "
                f"(通道位置={position_in_channel:.0%})"
            )
        elif price <= lower_threshold:
            sig_type = SignalType.SELL
            raw_conf = min(0.9, 0.4 + (lower_threshold - price) / atr * 0.2) if atr > 0 else 0.5
            reason = (
                f"跌破{self.lookback}日低点 {channel_low:.2f} "
                f"(通道位置={position_in_channel:.0%})"
            )
        else:
            sig_type = SignalType.HOLD
            raw_conf = abs(position_in_channel - 0.5) * 0.1
            reason = f"通道内 (位置={position_in_channel:.0%})"

        self.logger.debug(
            f"Breakout {symbol}: price={price:.2f}, "
            f"high={channel_high:.2f}, low={channel_low:.2f}, "
            f"ATR={atr:.2f}, signal={sig_type.value}, conf={raw_conf:.3f}"
        )

        return Signal(
            symbol=symbol,
            signal_type=sig_type,
            price=price,
            confidence=raw_conf,
            quantity=0,
            strategy_name="breakout",
            extra_data={
                "channel_high": channel_high,
                "channel_low": channel_low,
                "channel_position": position_in_channel,
                "atr": atr,
                "reason": reason,
            },
        )

    def _calc_atr(self, df) -> float:
        """计算 Average True Range"""
        if len(df) < self.atr_period + 1:
            return float(df["high"].iloc[-1] - df["low"].iloc[-1])

        recent = df.tail(self.atr_period + 1)
        highs = recent["high"].values.astype(float)
        lows = recent["low"].values.astype(float)
        closes = recent["close"].values.astype(float)

        tr_values = []
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_values.append(tr)

        return float(np.mean(tr_values)) if tr_values else 1.0

    async def _get_historical(self, symbol: str):
        """获取历史日K线"""
        try:
            df = await self.historical_loader.get_candlesticks(
                symbol, count=self.lookback + self.atr_period + 5, use_cache=True
            )
            if df is not None and not df.empty and "high" in df.columns:
                return df
        except Exception as e:
            self.logger.warning(f"Breakout 获取 {symbol} 历史数据失败: {e}")
        return None
