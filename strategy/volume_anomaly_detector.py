#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
实时异常成交量检测器

通过监控实时行情推送，检测机构交易痕迹：
1. Volume Surge - 累计成交量远超历史同时段均值
2. Volume Spike - 短窗口内成交量突然加速
3. Block Trade Inference - 连续行情推送间出现超大成交量跳变
4. Price-Volume Divergence - 量增价平，典型机构静默吸筹/出货模式
"""

import asyncio
import logging
import time
import numpy as np
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils import setup_logger


class AnomalyType(Enum):
    VOLUME_SURGE = "volume_surge"
    VOLUME_SPIKE = "volume_spike"
    BLOCK_TRADE = "block_trade"
    PRICE_VOLUME_DIVERGENCE = "price_volume_divergence"


@dataclass
class VolumeAnomaly:
    """一次检测到的异常事件"""
    symbol: str
    anomaly_type: AnomalyType
    timestamp: datetime
    confidence: float
    current_volume: int
    baseline_volume: float
    volume_ratio: float
    price: float
    price_change_pct: float
    details: str


@dataclass
class VolumeSignal:
    """异常成交量产生的交易信号"""
    symbol: str
    signal_type: str          # BUY / SELL
    confidence: float
    price: float
    anomalies: List[VolumeAnomaly] = field(default_factory=list)
    reason: str = ""


@dataclass
class SymbolVolumeProfile:
    """单个股票的成交量跟踪数据"""
    avg_daily_volume: float = 0.0
    std_daily_volume: float = 0.0
    avg_volume_by_hour: Dict[int, float] = field(default_factory=dict)
    volume_ticks: deque = field(default_factory=lambda: deque(maxlen=500))
    price_ticks: deque = field(default_factory=lambda: deque(maxlen=500))
    last_cumulative_volume: int = 0
    last_price: float = 0.0
    last_tick_time: float = 0.0
    anomaly_queue: List[VolumeAnomaly] = field(default_factory=list)
    daily_anomaly_count: int = 0
    last_anomaly_reset: datetime = field(default_factory=datetime.now)


class VolumeAnomalyDetector:
    """实时异常成交量检测器"""

    MAX_ANOMALIES_PER_DAY = 20   # 每只股票每日最大异常数，防止刷屏

    def __init__(self, config, realtime_mgr, hist_loader, logger: logging.Logger = None):
        self.config = config
        self.realtime_mgr = realtime_mgr
        self.hist_loader = hist_loader
        self.logger = logger or setup_logger(
            "volume_anomaly",
            config.get("logging.level", "INFO"),
            config.get("logging.file"),
        )

        # 配置
        self.surge_multiplier = config.get("volume_anomaly.surge_multiplier", 3.0)
        self.spike_std_threshold = config.get("volume_anomaly.spike_std_threshold", 2.5)
        self.block_trade_pct = config.get("volume_anomaly.block_trade_pct", 0.5) / 100.0
        self.divergence_volume_ratio = config.get("volume_anomaly.divergence_volume_ratio", 2.0)
        self.divergence_price_threshold = config.get("volume_anomaly.divergence_price_threshold", 0.003)
        self.lookback_days = config.get("volume_anomaly.lookback_days", 30)
        self.min_confidence = config.get("volume_anomaly.min_anomaly_confidence", 0.5)
        self.spike_window_seconds = 300  # 5-minute window for spike detection

        # 每个股票的跟踪状态
        self.profiles: Dict[str, SymbolVolumeProfile] = {}
        self._started = False

        self.logger.info(
            f"异常成交量检测器初始化: surge={self.surge_multiplier}x, "
            f"spike_std={self.spike_std_threshold}, "
            f"block_pct={self.block_trade_pct*100:.1f}%, "
            f"divergence_vol={self.divergence_volume_ratio}x"
        )

    # ----------------------------------------------------------
    # 启动 & 基线计算
    # ----------------------------------------------------------

    async def start(self, symbols: List[str]):
        """启动检测器：计算历史基线 + 注册实时回调"""
        self.logger.info(f"启动异常成交量检测器, 覆盖 {len(symbols)} 只股票...")

        for symbol in symbols:
            await self._build_baseline(symbol)

        self.realtime_mgr.register_callback("Quote", self._on_quote_sync_wrapper)
        self._started = True
        self.logger.info("异常成交量检测器已启动，实时监控中")

    async def _build_baseline(self, symbol: str):
        """从历史数据计算成交量基线"""
        profile = SymbolVolumeProfile()

        try:
            hist = await self.hist_loader.get_candlesticks(
                symbol, count=self.lookback_days, use_cache=True
            )

            if hist is not None and not hist.empty and 'volume' in hist.columns:
                volumes = hist['volume'].values.astype(float)
                volumes = volumes[volumes > 0]

                if len(volumes) >= 5:
                    profile.avg_daily_volume = float(np.mean(volumes))
                    profile.std_daily_volume = float(np.std(volumes))

                    # 粗略的按小时均量分布（假设交易时间 6.5h，均匀分布）
                    trading_hours = 6.5
                    hourly_avg = profile.avg_daily_volume / trading_hours
                    for h in range(24):
                        profile.avg_volume_by_hour[h] = hourly_avg

                    self.logger.info(
                        f"  {symbol} 基线: 日均量={profile.avg_daily_volume:,.0f}, "
                        f"标准差={profile.std_daily_volume:,.0f}"
                    )
                else:
                    profile.avg_daily_volume = 1_000_000
                    profile.std_daily_volume = 500_000
                    self.logger.warning(f"  {symbol} 历史数据不足，使用默认基线")
            else:
                profile.avg_daily_volume = 1_000_000
                profile.std_daily_volume = 500_000
                self.logger.warning(f"  {symbol} 无历史数据，使用默认基线")

        except Exception as e:
            profile.avg_daily_volume = 1_000_000
            profile.std_daily_volume = 500_000
            self.logger.error(f"  {symbol} 基线计算失败: {e}")

        self.profiles[symbol] = profile

    # ----------------------------------------------------------
    # 实时行情回调
    # ----------------------------------------------------------

    def _on_quote_sync_wrapper(self, symbol: str, quote: Any):
        """同步包装器，供 register_callback 使用"""
        # 在事件循环中调度异步处理
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._on_quote(symbol, quote))
        except RuntimeError:
            pass

    async def _on_quote(self, symbol: str, quote: Any):
        """每次行情推送时更新跟踪数据并检测异常"""
        if symbol not in self.profiles:
            return

        profile = self.profiles[symbol]
        now = time.time()
        ts = datetime.now()

        price = float(getattr(quote, 'last_done', 0))
        volume = int(getattr(quote, 'volume', 0))
        if price <= 0 or volume <= 0:
            return

        # 重置每日计数器
        if ts.date() != profile.last_anomaly_reset.date():
            profile.daily_anomaly_count = 0
            profile.last_anomaly_reset = ts

        # 计算成交量增量
        volume_delta = 0
        if profile.last_cumulative_volume > 0 and volume >= profile.last_cumulative_volume:
            volume_delta = volume - profile.last_cumulative_volume

        time_delta = now - profile.last_tick_time if profile.last_tick_time > 0 else 0

        # 记录 tick
        profile.volume_ticks.append((now, volume, volume_delta))
        profile.price_ticks.append((now, price))

        # 运行检测（需要至少有前一次数据）
        if profile.last_cumulative_volume > 0 and profile.daily_anomaly_count < self.MAX_ANOMALIES_PER_DAY:
            self._check_volume_surge(symbol, profile, volume, price, ts)
            self._check_volume_spike(symbol, profile, volume_delta, price, ts, now)
            self._check_block_trade(symbol, profile, volume_delta, price, ts)
            self._check_price_volume_divergence(symbol, profile, price, ts, now)

        profile.last_cumulative_volume = volume
        profile.last_price = price
        profile.last_tick_time = now

    # ----------------------------------------------------------
    # 检测算法
    # ----------------------------------------------------------

    def _check_volume_surge(self, symbol: str, profile: SymbolVolumeProfile,
                            cumulative_volume: int, price: float, ts: datetime):
        """检测 1: 累计成交量远超日均"""
        if profile.avg_daily_volume <= 0:
            return

        ratio = cumulative_volume / profile.avg_daily_volume
        if ratio < self.surge_multiplier:
            return

        # 按当前时间占交易时段的比例调整阈值
        hour = ts.hour
        # 美股交易时段大约 9:30-16:00 ET, 港股 9:30-16:00 HKT
        # 使用粗略的 fraction-of-day 校正
        trading_fraction = max(0.1, min(1.0, (hour - 9) / 6.5)) if 9 <= hour <= 16 else 1.0
        adjusted_threshold = self.surge_multiplier * trading_fraction

        if ratio < adjusted_threshold:
            return

        confidence = min(0.9, 0.4 + (ratio - adjusted_threshold) * 0.1)
        if confidence < self.min_confidence:
            return

        price_change = ((price - profile.price_ticks[0][1]) / profile.price_ticks[0][1]
                        if profile.price_ticks else 0)

        anomaly = VolumeAnomaly(
            symbol=symbol,
            anomaly_type=AnomalyType.VOLUME_SURGE,
            timestamp=ts,
            confidence=confidence,
            current_volume=cumulative_volume,
            baseline_volume=profile.avg_daily_volume,
            volume_ratio=ratio,
            price=price,
            price_change_pct=price_change,
            details=f"累计成交量 {cumulative_volume:,} = {ratio:.1f}x 日均量",
        )
        profile.anomaly_queue.append(anomaly)
        profile.daily_anomaly_count += 1
        self.logger.info(f"[SURGE] {symbol}: {anomaly.details}, 价格变动={price_change*100:.2f}%")

    def _check_volume_spike(self, symbol: str, profile: SymbolVolumeProfile,
                            volume_delta: int, price: float, ts: datetime, now: float):
        """检测 2: 短窗口内成交量突然加速"""
        if len(profile.volume_ticks) < 10:
            return

        # 计算过去 5 分钟内的成交量增速
        window_start = now - self.spike_window_seconds
        recent_deltas = [
            delta for t, _, delta in profile.volume_ticks
            if t >= window_start and delta > 0
        ]

        if len(recent_deltas) < 3:
            return

        # 全部 tick 的平均增量
        all_deltas = [delta for _, _, delta in profile.volume_ticks if delta > 0]
        if not all_deltas:
            return

        mean_delta = np.mean(all_deltas)
        std_delta = np.std(all_deltas) if len(all_deltas) > 2 else mean_delta * 0.5

        if std_delta <= 0:
            return

        recent_mean = np.mean(recent_deltas)
        z_score = (recent_mean - mean_delta) / std_delta

        if z_score < self.spike_std_threshold:
            return

        confidence = min(0.85, 0.4 + z_score * 0.08)
        if confidence < self.min_confidence:
            return

        anomaly = VolumeAnomaly(
            symbol=symbol,
            anomaly_type=AnomalyType.VOLUME_SPIKE,
            timestamp=ts,
            confidence=confidence,
            current_volume=int(recent_mean),
            baseline_volume=mean_delta,
            volume_ratio=recent_mean / mean_delta if mean_delta > 0 else 0,
            price=price,
            price_change_pct=0,
            details=f"5分钟成交速率异常 z={z_score:.1f}, 当前速率={recent_mean:,.0f}/tick vs 均值={mean_delta:,.0f}",
        )
        profile.anomaly_queue.append(anomaly)
        profile.daily_anomaly_count += 1
        self.logger.info(f"[SPIKE] {symbol}: {anomaly.details}")

    def _check_block_trade(self, symbol: str, profile: SymbolVolumeProfile,
                           volume_delta: int, price: float, ts: datetime):
        """检测 3: 单次推送间出现超大成交量跳变（推断大宗交易）"""
        if profile.avg_daily_volume <= 0 or volume_delta <= 0:
            return

        block_threshold = profile.avg_daily_volume * self.block_trade_pct
        if volume_delta < block_threshold:
            return

        ratio = volume_delta / profile.avg_daily_volume
        confidence = min(0.9, 0.5 + ratio * 2)
        if confidence < self.min_confidence:
            return

        anomaly = VolumeAnomaly(
            symbol=symbol,
            anomaly_type=AnomalyType.BLOCK_TRADE,
            timestamp=ts,
            confidence=confidence,
            current_volume=volume_delta,
            baseline_volume=profile.avg_daily_volume,
            volume_ratio=ratio,
            price=price,
            price_change_pct=0,
            details=f"疑似大宗交易: 单次成交量 {volume_delta:,} = 日均量的 {ratio*100:.2f}%",
        )
        profile.anomaly_queue.append(anomaly)
        profile.daily_anomaly_count += 1
        self.logger.info(f"[BLOCK] {symbol}: {anomaly.details}, 价格={price}")

    def _check_price_volume_divergence(self, symbol: str, profile: SymbolVolumeProfile,
                                       price: float, ts: datetime, now: float):
        """检测 4: 量增价平 —— 机构静默吸筹/出货"""
        if len(profile.price_ticks) < 20 or len(profile.volume_ticks) < 20:
            return

        # 过去 5 分钟的价格变动
        window_start = now - self.spike_window_seconds
        recent_prices = [p for t, p in profile.price_ticks if t >= window_start]
        recent_volumes = [d for t, _, d in profile.volume_ticks if t >= window_start and d > 0]

        if len(recent_prices) < 5 or len(recent_volumes) < 5:
            return

        price_change = abs(recent_prices[-1] - recent_prices[0]) / recent_prices[0] if recent_prices[0] > 0 else 0

        # 价格几乎不动
        if price_change > self.divergence_price_threshold:
            return

        # 但成交量远超正常水平
        all_deltas = [d for _, _, d in profile.volume_ticks if d > 0]
        if not all_deltas:
            return

        overall_mean = np.mean(all_deltas)
        recent_mean = np.mean(recent_volumes)

        volume_ratio = recent_mean / overall_mean if overall_mean > 0 else 0
        if volume_ratio < self.divergence_volume_ratio:
            return

        confidence = min(0.85, 0.45 + (volume_ratio - self.divergence_volume_ratio) * 0.1)
        if confidence < self.min_confidence:
            return

        # 判断微弱的价格方向
        slight_direction = recent_prices[-1] - recent_prices[0]

        anomaly = VolumeAnomaly(
            symbol=symbol,
            anomaly_type=AnomalyType.PRICE_VOLUME_DIVERGENCE,
            timestamp=ts,
            confidence=confidence,
            current_volume=int(recent_mean),
            baseline_volume=overall_mean,
            volume_ratio=volume_ratio,
            price=price,
            price_change_pct=price_change * (1 if slight_direction >= 0 else -1),
            details=(f"量增价平: 价格变动仅{price_change*100:.3f}%, "
                     f"但成交速率={volume_ratio:.1f}x均值, "
                     f"方向={'偏多' if slight_direction > 0 else '偏空'}"),
        )
        profile.anomaly_queue.append(anomaly)
        profile.daily_anomaly_count += 1
        self.logger.info(f"[DIVERGE] {symbol}: {anomaly.details}")

    # ----------------------------------------------------------
    # 信号生成（由周期任务调用）
    # ----------------------------------------------------------

    async def check_and_generate_signals(self) -> List[VolumeSignal]:
        """整合异常队列，生成交易信号"""
        signals = []

        for symbol, profile in self.profiles.items():
            if not profile.anomaly_queue:
                continue

            anomalies = profile.anomaly_queue.copy()
            profile.anomaly_queue.clear()

            signal = self._anomalies_to_signal(symbol, anomalies)
            if signal and signal.confidence >= self.min_confidence:
                signals.append(signal)

        return signals

    def _anomalies_to_signal(self, symbol: str, anomalies: List[VolumeAnomaly]) -> Optional[VolumeSignal]:
        """将一组异常事件转化为一个交易信号"""
        if not anomalies:
            return None

        # 加权置信度：不同类型异常有不同权重
        type_weights = {
            AnomalyType.PRICE_VOLUME_DIVERGENCE: 1.5,   # 最有信号价值
            AnomalyType.BLOCK_TRADE: 1.3,
            AnomalyType.VOLUME_SPIKE: 1.0,
            AnomalyType.VOLUME_SURGE: 0.8,
        }

        total_weight = 0
        weighted_confidence = 0
        net_direction = 0  # >0 偏多, <0 偏空
        reasons = []

        for a in anomalies:
            w = type_weights.get(a.anomaly_type, 1.0)
            weighted_confidence += a.confidence * w
            total_weight += w

            # 方向推断
            if a.anomaly_type == AnomalyType.PRICE_VOLUME_DIVERGENCE:
                net_direction += (1 if a.price_change_pct >= 0 else -1) * w * 2
            elif a.price_change_pct > 0.005:
                net_direction += w
            elif a.price_change_pct < -0.005:
                net_direction -= w

            reasons.append(f"[{a.anomaly_type.value}] {a.details}")

        if total_weight <= 0:
            return None

        avg_confidence = weighted_confidence / total_weight
        # 多个异常叠加提升置信度
        boost = min(0.15, len(anomalies) * 0.03)
        final_confidence = min(0.95, avg_confidence + boost)

        latest_price = anomalies[-1].price

        # 如果方向不明确，默认按成交量方向 + 价格微动判断
        if abs(net_direction) < 0.5:
            avg_price_change = np.mean([a.price_change_pct for a in anomalies])
            net_direction = 1 if avg_price_change >= 0 else -1

        signal_type = "BUY" if net_direction > 0 else "SELL"

        return VolumeSignal(
            symbol=symbol,
            signal_type=signal_type,
            confidence=final_confidence,
            price=latest_price,
            anomalies=anomalies,
            reason="; ".join(reasons[:3]),
        )

    # ----------------------------------------------------------
    # 摘要
    # ----------------------------------------------------------

    def get_summary(self) -> str:
        """获取当前检测状态摘要"""
        lines = ["📊 异常成交量检测器摘要:"]
        lines.append(f"  监控股票: {len(self.profiles)} 只")

        active = 0
        for symbol, profile in self.profiles.items():
            pending = len(profile.anomaly_queue)
            daily = profile.daily_anomaly_count
            if daily > 0 or pending > 0:
                active += 1
                lines.append(f"  {symbol}: 今日异常={daily}, 待处理={pending}, "
                           f"日均量={profile.avg_daily_volume:,.0f}")

        if active == 0:
            lines.append("  当前无异常")

        return '\n'.join(lines)
