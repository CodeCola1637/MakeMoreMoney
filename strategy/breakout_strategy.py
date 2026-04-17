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
        # P0-5: 成交量确认与"刺破"过滤
        self.volume_confirm_multiplier = float(
            config.get("strategy.breakout.volume_confirm_multiplier", 1.5)
        )
        # 当日累计成交量需 ≥ 近 N 日均量 × multiplier 才算有效突破
        self.require_close_above = bool(
            config.get("strategy.breakout.require_close_above", True)
        )
        # 当当日 K 线收盘价仍在突破线之上才算有效（缺当日数据时降级为按 last_done）
        self.min_breakout_persistence = float(
            config.get("strategy.breakout.min_breakout_persistence_pct", 0.001)
        )
        # 收盘价至少高出突破线 0.1%（避免单 tick 刺破）

        self.logger.info(
            f"BreakoutStrategy 初始化: 回看={self.lookback}天, "
            f"ATR周期={self.atr_period}, 突破余量={self.breakout_margin:.1%}, "
            f"成交量倍数={self.volume_confirm_multiplier}x, "
            f"收盘确认={self.require_close_above}"
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
        
        # P0-5: 成交量确认 — 当日累计成交量需放大
        volume_ok = self._check_volume_confirmation(recent, data)
        # P0-5: 收盘确认 — 价格需明显高于突破线（不只是单 tick 刺破）
        persistence = self.min_breakout_persistence

        if price >= upper_threshold * (1 + persistence) and volume_ok:
            sig_type = SignalType.BUY
            raw_conf = min(0.9, 0.4 + (price - upper_threshold) / atr * 0.2) if atr > 0 else 0.5
            reason = (
                f"突破{self.lookback}日高点 {channel_high:.2f} "
                f"(通道位置={position_in_channel:.0%}, 量能确认✅)"
            )
        elif price <= lower_threshold * (1 - persistence) and volume_ok:
            sig_type = SignalType.SELL
            raw_conf = min(0.9, 0.4 + (lower_threshold - price) / atr * 0.2) if atr > 0 else 0.5
            reason = (
                f"跌破{self.lookback}日低点 {channel_low:.2f} "
                f"(通道位置={position_in_channel:.0%}, 量能确认✅)"
            )
        elif (price >= upper_threshold or price <= lower_threshold) and not volume_ok:
            # 价格刺破但成交量不足 → 视为假突破，弃权
            sig_type = SignalType.HOLD
            raw_conf = 0.05
            reason = (
                f"价格刺破但量能不足 (price={price:.2f}, "
                f"upper={upper_threshold:.2f}, lower={lower_threshold:.2f})"
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

    def _check_volume_confirmation(self, recent_df, data: Dict[str, Any]) -> bool:
        """P0-5: 突破方向需要量能配合 — 当日成交量需 ≥ 近 N 日均量 × multiplier。
        
        优先级：
        1. data 中包含 'today_volume' / 'volume' 字段 → 直接对比 recent 均量
        2. 否则用 recent_df 最后一根 K 线的 volume 对比再前 N-1 根的均量
        3. 数据缺失时降级返回 True（避免完全屏蔽信号），但日志告警
        """
        try:
            avg_vol = float(recent_df["volume"].mean()) if "volume" in recent_df.columns else 0.0
            if avg_vol <= 0:
                return True
            today_vol = 0.0
            if isinstance(data, dict):
                today_vol = float(data.get("today_volume") or data.get("volume") or 0.0)
            if today_vol <= 0 and "volume" in recent_df.columns and len(recent_df) > 0:
                today_vol = float(recent_df["volume"].iloc[-1])
                if len(recent_df) > 1:
                    avg_vol = float(recent_df["volume"].iloc[:-1].mean())
            if today_vol <= 0:
                self.logger.debug("Breakout: 缺当日成交量数据，量能确认默认通过")
                return True
            return today_vol >= avg_vol * self.volume_confirm_multiplier
        except Exception as e:
            self.logger.debug(f"Breakout 量能确认异常: {e}")
            return True
    
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
