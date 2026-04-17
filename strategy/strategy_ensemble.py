#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
策略组合器
支持多个策略的加权融合，提供更稳定和多样化的交易信号
"""

import asyncio
import logging
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

from strategy.signals import Signal, SignalType
from strategy.signal_filter import SignalFilter
from utils import setup_logger


class EnsembleMethod(Enum):
    """组合方法"""
    EQUAL_WEIGHT = "equal_weight"           # 等权重
    CONFIDENCE_WEIGHT = "confidence_weight" # 置信度加权
    PERFORMANCE_WEIGHT = "performance_weight" # 历史表现加权
    DYNAMIC_WEIGHT = "dynamic_weight"       # 动态权重


@dataclass
class StrategyPerformance:
    """策略性能指标"""
    name: str
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    recent_accuracy: float = 0.0  # 最近20次信号的准确率
    signal_count: int = 0
    last_update: datetime = None


class StrategyEnsemble:
    """策略组合器"""
    
    def __init__(self, config, strategies: Dict[str, Any], ensemble_method: EnsembleMethod = EnsembleMethod.CONFIDENCE_WEIGHT, order_manager=None, profit_stop_mgr=None):
        """
        初始化策略组合器
        
        Args:
            config: 配置对象
            strategies: 策略字典 {strategy_name: strategy_instance}
            ensemble_method: 组合方法
            order_manager: 订单管理器实例（用于获取实时账户余额）
            profit_stop_mgr: 止盈止损管理器实例（用于阻止在即将止损时买入）
        """
        self.config = config
        self.strategies = strategies
        self.ensemble_method = ensemble_method
        self._order_manager = order_manager
        self._profit_stop_mgr = profit_stop_mgr
        
        # 设置日志
        self.logger = setup_logger(
            "strategy_ensemble",
            self.config.get("logging.level", "INFO"),
            self.config.get("logging.file")
        )
        
        # 策略权重
        self.strategy_weights: Dict[str, float] = {}
        self.initialize_weights()
        
        # 策略性能跟踪
        self.performance_tracker: Dict[str, StrategyPerformance] = {}
        self.initialize_performance_tracker()
        
        # 信号历史
        self.signal_history: List[Dict] = []
        self.max_history_length = 1000
        
        # 组合配置
        self.min_strategies_agreement = config.get("ensemble.min_strategies_agreement", 2)
        self.confidence_threshold = config.get("ensemble.confidence_threshold", 0.1)
        self.reweight_frequency = config.get("ensemble.reweight_frequency", 100)
        self.signal_count = 0
        
        # 触发策略 vs 辅助策略
        self.trigger_strategies = set(config.get("ensemble.trigger_strategies", []))
        self.auxiliary_strategies = set(config.get("ensemble.auxiliary_strategies", []))
        if self.trigger_strategies:
            self.logger.info(f"触发策略（可发起交易）: {self.trigger_strategies}")
            self.logger.info(f"辅助策略（仅确认方向）: {self.auxiliary_strategies}")
        
        # P1-8: 波动率自适应仓位
        # position_scale = clip(target_vol / actual_vol, min, max)
        self._vol_target_pct = float(config.get("ensemble.position_sizing.target_vol_pct", 1.5))
        self._vol_scale_min = float(config.get("ensemble.position_sizing.scale_min", 0.5))
        self._vol_scale_max = float(config.get("ensemble.position_sizing.scale_max", 1.3))
        self._vol_enabled = bool(config.get("ensemble.position_sizing.vol_adaptive", True))
        # 缓存近期波动率（symbol -> (cached_at, vol_pct)）
        self._vol_cache: Dict[str, Tuple[datetime, float]] = {}
        if self._vol_enabled:
            self.logger.info(
                f"波动率自适应仓位启用: target_vol={self._vol_target_pct}%, "
                f"scale=[{self._vol_scale_min}, {self._vol_scale_max}]"
            )
        
        # P1-9: 信号一致性追踪 — symbol -> [SignalType.value, ...] 最近 5 次方向
        from collections import deque, defaultdict
        self._direction_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=5))
        
        # P2-13: Pyramid 加仓
        self._pyramid_enabled = bool(config.get("ensemble.pyramid.enable", True))
        self._pyramid_min_profit_pct = float(config.get("ensemble.pyramid.min_profit_pct", 5.0))
        self._pyramid_boost_pct = float(config.get("ensemble.pyramid.boost_pct", 0.5))
        if self._pyramid_enabled:
            self.logger.info(
                f"Pyramid 加仓启用: 浮盈 ≥ {self._pyramid_min_profit_pct}% 且同向触发 "
                f"→ 加仓 {self._pyramid_boost_pct*100:.0f}%"
            )
        
        # P2-15: 策略权重 PnL 反馈状态
        self._weight_feedback_enabled = bool(config.get("ensemble.weight_feedback.enable", True))
        self._weight_feedback_lookback_days = int(config.get("ensemble.weight_feedback.lookback_days", 14))
        self._weight_feedback_min_trades = int(config.get("ensemble.weight_feedback.min_trades_per_source", 3))
        self._weight_feedback_alpha = float(config.get("ensemble.weight_feedback.alpha", 0.3))
        self._weight_feedback_orders_csv = str(config.get("logging.orders_csv", "logs/orders.csv"))
        self._weight_feedback_interval = timedelta(hours=int(config.get("ensemble.weight_feedback.refresh_hours", 24)))
        self._weight_feedback_last_run: Optional[datetime] = None
        self._weight_baseline: Dict[str, float] = {}  # 在 initialize_weights 后填充
        
        # 信号过滤器
        self.signal_filter = SignalFilter(config, self.logger)
        
        self.logger.info(f"策略组合器初始化完成 - 方法: {ensemble_method.value}, 策略数: {len(strategies)}")
        
    def initialize_weights(self):
        """初始化策略权重（优先使用配置文件中的自定义权重）"""
        custom_weights = self.config.get("ensemble.strategy_weights", {})

        if custom_weights and isinstance(custom_weights, dict):
            for strategy_name in self.strategies.keys():
                self.strategy_weights[strategy_name] = custom_weights.get(
                    strategy_name, 1.0 / len(self.strategies)
                )
            self._normalize_weights()
            self.logger.info(f"使用配置自定义权重: {self.strategy_weights}")
        elif self.ensemble_method == EnsembleMethod.EQUAL_WEIGHT:
            weight = 1.0 / len(self.strategies)
            for strategy_name in self.strategies.keys():
                self.strategy_weights[strategy_name] = weight
        else:
            weight = 1.0 / len(self.strategies)
            for strategy_name in self.strategies.keys():
                self.strategy_weights[strategy_name] = weight

        self.logger.info(f"初始策略权重: {self.strategy_weights}")
        # P2-15: 记录基线权重，PnL 反馈以此为锚
        self._weight_baseline = dict(self.strategy_weights)
        
    def initialize_performance_tracker(self):
        """初始化性能跟踪器"""
        for strategy_name in self.strategies.keys():
            self.performance_tracker[strategy_name] = StrategyPerformance(
                name=strategy_name,
                last_update=datetime.now()
            )
            
    async def generate_ensemble_signal(self, symbol: str, data: Dict[str, Any]) -> Optional[Signal]:
        """
        生成组合信号
        
        Args:
            symbol: 股票代码
            data: 市场数据
            
        Returns:
            组合后的交易信号
        """
        try:
            # P2-15: 周期性根据已成交订单胜率刷新策略权重
            self._maybe_refresh_weights_from_pnl()
            
            # 收集各策略的信号
            strategy_signals = await self._collect_strategy_signals(symbol, data)
            
            if not strategy_signals:
                self.logger.warning(f"没有策略产生信号: {symbol}")
                return None
                
            # 过滤有效信号
            valid_signals = self._filter_valid_signals(strategy_signals)
            
            if len(valid_signals) < self.min_strategies_agreement:
                self.logger.debug(f"有效信号数量不足: {len(valid_signals)} < {self.min_strategies_agreement}")
                return None
                
            # 生成组合信号
            ensemble_signal = self._combine_signals(valid_signals, symbol, data)
            
            if ensemble_signal is None:
                return None
            
            # 阻止在即将止损/止盈退出时买入同一标的
            if (ensemble_signal.signal_type == SignalType.BUY
                    and self._profit_stop_mgr
                    and self._profit_stop_mgr.is_near_exit(symbol)):
                self.logger.info(
                    f"信号被过滤: {symbol} - 该标的即将触发止盈/止损退出，阻止买入"
                )
                return None

            # 信号过滤检查（通过时原子性记录，防止并发竞态）
            should_emit, filter_reason = await self.signal_filter.should_emit_signal(ensemble_signal)
            if not should_emit:
                self.logger.info(f"信号被过滤: {symbol} - {filter_reason}")
                return None
            
            # 记录信号历史
            self._record_signal_history(strategy_signals, ensemble_signal)
            
            # 更新信号计数
            self.signal_count += 1
            
            # 定期重新计算权重
            if self.signal_count % self.reweight_frequency == 0:
                await self._reweight_strategies()
            
            self.logger.info(f"✅ 信号通过过滤: {symbol} {ensemble_signal.signal_type.value} (今日第{self.signal_filter.get_signal_count_today(symbol)}次)")
                
            return ensemble_signal
            
        except Exception as e:
            self.logger.error(f"生成组合信号失败: {e}")
            return None
            
    async def _collect_strategy_signals(self, symbol: str, data: Dict[str, Any]) -> Dict[str, Signal]:
        """收集各策略的信号（None 表示弃权，不参与投票）"""
        strategy_signals = {}
        abstained = []
        
        tasks = []
        for strategy_name, strategy in self.strategies.items():
            task = asyncio.create_task(
                self._get_strategy_signal(strategy_name, strategy, symbol, data)
            )
            tasks.append(task)
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, (strategy_name, strategy) in enumerate(self.strategies.items()):
            result = results[i]
            if isinstance(result, Exception):
                self.logger.error(f"策略 {strategy_name} 信号生成失败: {result}")
                abstained.append(strategy_name)
            elif result is None:
                abstained.append(strategy_name)
                self.logger.debug(f"策略 {strategy_name} 弃权（无 {symbol} 数据）")
            else:
                strategy_signals[strategy_name] = result
                self.logger.debug(f"策略 {strategy_name} 信号: {result.signal_type.value}, 置信度: {result.confidence:.3f}")
        
        if abstained:
            self.logger.info(f"{symbol} 弃权策略: {abstained}, 参与投票: {list(strategy_signals.keys())}")
                
        return strategy_signals
        
    async def _get_strategy_signal(self, strategy_name: str, strategy: Any, symbol: str, data: Dict[str, Any]) -> Optional[Signal]:
        """获取单个策略的信号"""
        try:
            # 根据策略类型调用不同的方法
            if hasattr(strategy, 'predict_and_generate_signal'):
                # SignalGenerator类型
                return await strategy.predict_and_generate_signal(symbol)
            elif hasattr(strategy, 'generate_signal'):
                # 其他策略类型
                return await strategy.generate_signal(symbol, data)
            elif hasattr(strategy, 'predict'):
                # 基础预测接口
                prediction = await strategy.predict(symbol, data)
                # 转换为Signal对象
                return self._prediction_to_signal(strategy_name, symbol, prediction, data)
            else:
                self.logger.warning(f"策略 {strategy_name} 没有可用的信号生成方法")
                return None
                
        except Exception as e:
            self.logger.error(f"策略 {strategy_name} 信号生成异常: {e}")
            return None
            
    def _prediction_to_signal(self, strategy_name: str, symbol: str, prediction: Any, data: Dict[str, Any]) -> Signal:
        """将预测结果转换为Signal对象"""
        try:
            if isinstance(prediction, dict):
                signal_type = SignalType(prediction.get('signal', 'HOLD'))
                confidence = prediction.get('confidence', 0.1)
                price = prediction.get('price', data.get('last_done', 0))
            else:
                # 假设prediction是数值型预测
                buy_threshold = 0.04
                sell_threshold = -0.04
                
                if prediction > buy_threshold:
                    signal_type = SignalType.BUY
                elif prediction < sell_threshold:
                    signal_type = SignalType.SELL
                else:
                    signal_type = SignalType.HOLD
                    
                confidence = abs(prediction)
                price = data.get('last_done', 0)
                
            return Signal(
                symbol=symbol,
                signal_type=signal_type,
                price=price,
                confidence=confidence,
                quantity=100,  # 默认数量
                strategy_name=f"ensemble_{strategy_name}"
            )
            
        except Exception as e:
            self.logger.error(f"预测结果转换失败: {e}")
            return Signal(
                symbol=symbol,
                signal_type=SignalType.HOLD,
                price=data.get('last_done', 0),
                confidence=0.0,
                quantity=0,
                strategy_name=f"ensemble_{strategy_name}"
            )
            
    def _filter_valid_signals(self, strategy_signals: Dict[str, Signal]) -> Dict[str, Signal]:
        """P2-12: 双门禁分层职责 ——
        
        ensemble.confidence_threshold = "输入门"：过滤 *单一策略* 的极弱信号
            （让其不参与加权，避免噪声拉高合成置信度）
            建议保持极低（≤ 0.05），仅剔除 0 / 极小置信度信号。
        
        signal_filter.confidence_threshold = "输出门"：过滤 *最终合成* 信号
            是否值得下单。建议 ≥ 0.08。
        """
        valid_signals = {}
        for strategy_name, signal in strategy_signals.items():
            if signal.confidence >= self.confidence_threshold and signal.price > 0:
                valid_signals[strategy_name] = signal
                self.logger.debug(f"策略 {strategy_name} 有效信号: {signal.signal_type.value}, 置信度: {signal.confidence:.3f}")
            else:
                self.logger.debug(
                    f"策略 {strategy_name} 输入门过滤: "
                    f"置信度 {signal.confidence:.3f} < ensemble.confidence_threshold={self.confidence_threshold} "
                    f"(注意：此为输入门，最终是否下单由 signal_filter 输出门决定)"
                )
        return valid_signals
        
    def _combine_signals(self, valid_signals: Dict[str, Signal], symbol: str, data: Dict[str, Any]) -> Signal:
        """组合多个信号（触发策略 + 辅助策略模式）。
        
        P0-3: 收集所有触发策略并检测多触发冲突；冲突时按辅助投票决定方向，
              辅助也分歧时强制 HOLD，避免少数派噪声主导决策。
        """
        try:
            # ── 触发检查：只有触发策略产生 BUY/SELL 才能发起交易 ──
            triggered_signals: Dict[str, Signal] = {}
            if self.trigger_strategies:
                for sname, sig in valid_signals.items():
                    if sname in self.trigger_strategies and sig.signal_type in (SignalType.BUY, SignalType.SELL):
                        triggered_signals[sname] = sig
                
                if not triggered_signals:
                    aux_dirs = {sn: s.signal_type.value for sn, s in valid_signals.items()
                                if sn in self.auxiliary_strategies and s.signal_type != SignalType.HOLD}
                    if aux_dirs:
                        self.logger.info(f"⏸ 辅助策略有方向 {aux_dirs}，但无触发策略信号，不交易: {symbol}")
                    return Signal(
                        symbol=symbol, signal_type=SignalType.HOLD,
                        price=data.get('last_done', 0), confidence=0.05,
                        quantity=0, strategy_name="ensemble_no_trigger"
                    )
                
                # P0-3: 多触发冲突检测
                trigger_buys = [s for s in triggered_signals.values() if s.signal_type == SignalType.BUY]
                trigger_sells = [s for s in triggered_signals.values() if s.signal_type == SignalType.SELL]
                if trigger_buys and trigger_sells:
                    aux_buy_w = sum(self.strategy_weights.get(sn, 0) for sn, s in valid_signals.items()
                                    if sn in self.auxiliary_strategies and s.signal_type == SignalType.BUY)
                    aux_sell_w = sum(self.strategy_weights.get(sn, 0) for sn, s in valid_signals.items()
                                     if sn in self.auxiliary_strategies and s.signal_type == SignalType.SELL)
                    aux_dominant = None
                    if aux_buy_w > aux_sell_w * 1.2:
                        aux_dominant = SignalType.BUY
                    elif aux_sell_w > aux_buy_w * 1.2:
                        aux_dominant = SignalType.SELL
                    
                    if aux_dominant is None:
                        self.logger.warning(
                            f"⚠️ 触发策略冲突且辅助分歧 → 强制 HOLD: {symbol} "
                            f"(BUY 触发={[s.strategy_name for s in trigger_buys]}, "
                            f"SELL 触发={[s.strategy_name for s in trigger_sells]}, "
                            f"aux_buy_w={aux_buy_w:.2f}, aux_sell_w={aux_sell_w:.2f})"
                        )
                        return Signal(
                            symbol=symbol, signal_type=SignalType.HOLD,
                            price=data.get('last_done', 0), confidence=0.05,
                            quantity=0, strategy_name="ensemble_trigger_conflict",
                            extra_data={
                                'trigger_buy': [s.strategy_name for s in trigger_buys],
                                'trigger_sell': [s.strategy_name for s in trigger_sells],
                                'reason': 'trigger_conflict_aux_split'
                            }
                        )
                    else:
                        # 按辅助主导方向裁决：剔除反向触发，仅保留同向触发
                        keep_type = aux_dominant
                        triggered_signals = {sn: s for sn, s in triggered_signals.items()
                                              if s.signal_type == keep_type}
                        self.logger.warning(
                            f"⚖️ 触发策略冲突 → 按辅助主导方向 {keep_type.value} 裁决: {symbol} "
                            f"(保留 {list(triggered_signals.keys())})"
                        )
                
                for sn, s in triggered_signals.items():
                    self.logger.info(
                        f"🔔 触发策略 [{sn}] 发出 {s.signal_type.value} 信号: "
                        f"{symbol}, 置信度={s.confidence:.3f}"
                    )

            # ── 正常加权组合（所有参与策略） ──
            participating = list(valid_signals.keys())
            raw_weights = {s: self.strategy_weights.get(s, 0.0) for s in participating}
            total_raw = sum(raw_weights.values())
            if total_raw > 0:
                norm_weights = {s: w / total_raw for s, w in raw_weights.items()}
            else:
                norm_weights = {s: 1.0 / len(participating) for s in participating}

            buy_signals = []
            sell_signals = []
            hold_signals = []
            confidences = []
            prices = []
            
            for strategy_name, signal in valid_signals.items():
                strategy_weight = norm_weights[strategy_name]
                weighted_confidence = signal.confidence * strategy_weight
                
                if signal.signal_type == SignalType.BUY:
                    buy_signals.append(weighted_confidence)
                elif signal.signal_type == SignalType.SELL:
                    sell_signals.append(weighted_confidence)
                elif signal.signal_type == SignalType.HOLD:
                    hold_signals.append(weighted_confidence)
                    
                confidences.append(weighted_confidence)
                prices.append(signal.price)
                
            buy_strength = sum(buy_signals)
            sell_strength = sum(sell_signals)
            hold_strength = sum(hold_signals)
            total_confidence = sum(confidences)
            
            if buy_strength > sell_strength and buy_strength > hold_strength and buy_strength > 0:
                signal_type = SignalType.BUY
                final_confidence = buy_strength
            elif sell_strength > buy_strength and sell_strength > hold_strength and sell_strength > 0:
                signal_type = SignalType.SELL
                final_confidence = sell_strength
            else:
                signal_type = SignalType.HOLD
                final_confidence = max(hold_strength, 0.05)
                
            # 计算平均价格
            avg_price = np.mean(prices) if prices else data.get('last_done', 0)
            
            # Lot-aware quantity calculation
            target_value = self.config.get("execution.risk_control.position_pct", 5.0) / 100.0
            total_equity = self.config.get("execution.initial_capital", 15000.0)
            if self._order_manager:
                try:
                    real_equity = self._order_manager.get_account_balance()
                    if real_equity > 0:
                        total_equity = real_equity
                except Exception:
                    pass
            try:
                max_trade_value = total_equity * target_value
            except Exception:
                max_trade_value = 750.0
            
            if avg_price > 0:
                raw_quantity = int(max_trade_value / avg_price)
            else:
                raw_quantity = 100
            
            lot_size = 1
            if '.HK' in symbol:
                if self._order_manager:
                    try:
                        lot_size = self._order_manager.get_lot_size(symbol)
                    except Exception:
                        lot_size = self.config.get("execution.lot_sizes", {}).get(symbol, 100)
                else:
                    lot_size = self.config.get("execution.lot_sizes", {}).get(symbol, 100)
            
            suggested_quantity = max(lot_size, (raw_quantity // lot_size) * lot_size)
            
            confidence_scale = max(0.5, min(1.0, total_confidence))
            # P1-8: 波动率自适应仓位 — 高波动收紧、低波动放大
            vol_scale = self._get_vol_scale(symbol, data)
            combined_scale = confidence_scale * vol_scale
            suggested_quantity = max(lot_size, int(suggested_quantity * combined_scale))
            
            # P2-13: Pyramid 加仓 — 已盈利 ≥ 5% 且同向再次触发时加仓 50%（受 fund_guard 单标的上限保护）
            pyramid_boost = 1.0
            if (self._profit_stop_mgr is not None
                    and signal_type == SignalType.BUY
                    and self._pyramid_enabled):
                try:
                    status = self._profit_stop_mgr.position_status.get(symbol)
                    if status and status.quantity > 0 and status.unrealized_pnl_pct >= self._pyramid_min_profit_pct:
                        pyramid_boost = 1.0 + self._pyramid_boost_pct
                        suggested_quantity = int(suggested_quantity * pyramid_boost)
                        self.logger.info(
                            f"🔺 Pyramid 加仓触发: {symbol} 已盈利 "
                            f"{status.unrealized_pnl_pct:.2f}% ≥ {self._pyramid_min_profit_pct}% "
                            f"→ quantity × {pyramid_boost:.2f}"
                        )
                except Exception as e:
                    self.logger.debug(f"Pyramid 检查失败 {symbol}: {e}")
            
            suggested_quantity = (suggested_quantity // lot_size) * lot_size
            if suggested_quantity <= 0:
                suggested_quantity = lot_size
            
            # 识别触发源（仅记录与最终方向一致的触发策略，便于事后归因）
            trigger_sources = [sn for sn, s in valid_signals.items()
                                if sn in self.trigger_strategies
                                and s.signal_type == signal_type]
            # 若最终为 HOLD，则用所有触发到的方向供调试
            if not trigger_sources:
                trigger_sources = [sn for sn, s in valid_signals.items()
                                   if sn in self.trigger_strategies
                                   and s.signal_type in (SignalType.BUY, SignalType.SELL)]
            aux_agree = [sn for sn in valid_signals if sn in self.auxiliary_strategies
                         and valid_signals[sn].signal_type == signal_type]
            aux_oppose = [sn for sn in valid_signals if sn in self.auxiliary_strategies
                          and valid_signals[sn].signal_type != SignalType.HOLD
                          and valid_signals[sn].signal_type != signal_type]
            
            # P0-4: 辅助反对参与决策
            # - 若辅助反对加权 > 触发加权 50% → confidence × 0.7
            # - 若辅助反对加权 > 触发加权 100% (即多于触发) → 强制 HOLD
            if signal_type in (SignalType.BUY, SignalType.SELL) and (aux_agree or aux_oppose):
                trigger_weight = sum(self.strategy_weights.get(sn, 0)
                                      for sn in trigger_sources)
                aux_oppose_weight = sum(self.strategy_weights.get(sn, 0) for sn in aux_oppose)
                aux_agree_weight = sum(self.strategy_weights.get(sn, 0) for sn in aux_agree)
                
                if trigger_weight > 0:
                    oppose_ratio = aux_oppose_weight / trigger_weight
                    if oppose_ratio >= 1.0 and aux_agree_weight < aux_oppose_weight:
                        self.logger.warning(
                            f"⚠️ 辅助反对加权({aux_oppose_weight:.2f}) ≥ 触发加权({trigger_weight:.2f}) "
                            f"且超过同意加权 → 强制 HOLD: {symbol}"
                        )
                        signal_type = SignalType.HOLD
                        final_confidence = 0.05
                    elif oppose_ratio >= 0.5:
                        new_conf = final_confidence * 0.7
                        self.logger.info(
                            f"⚠️ 辅助反对加权({aux_oppose_weight:.2f}) > 50% 触发加权 "
                            f"({trigger_weight:.2f}) → confidence {final_confidence:.3f} → {new_conf:.3f}: {symbol}"
                        )
                        final_confidence = new_conf

            # P1-9: 信号一致性加成 — 连续 3 次同向触发 → confidence × 1.2（上限 0.95）
            consistency_boost = 1.0
            if signal_type in (SignalType.BUY, SignalType.SELL):
                hist = self._direction_history[symbol]
                hist.append(signal_type.value)
                same_direction = sum(1 for d in hist if d == signal_type.value)
                if same_direction >= 3:
                    consistency_boost = 1.2
                    final_confidence = min(0.95, final_confidence * consistency_boost)
                    self.logger.info(
                        f"📈 信号一致性加成: {symbol} 连续 {same_direction} 次 "
                        f"{signal_type.value} → confidence × 1.2 = {final_confidence:.3f}"
                    )
            elif signal_type == SignalType.HOLD:
                # 出现 HOLD 不清空但记录，避免被反向噪声打断节奏
                self._direction_history[symbol].append('HOLD')
            
            ensemble_signal = Signal(
                symbol=symbol,
                signal_type=signal_type,
                price=avg_price,
                confidence=final_confidence,
                quantity=suggested_quantity,
                strategy_name="ensemble",
                extra_data={
                    'buy_strength': buy_strength,
                    'sell_strength': sell_strength,
                    'hold_strength': hold_strength,
                    'total_confidence': total_confidence,
                    'contributing_strategies': list(valid_signals.keys()),
                    'strategy_weights': {k: round(v, 3) for k, v in norm_weights.items()},
                    'trigger_sources': trigger_sources,
                    'auxiliary_agree': aux_agree,
                    'auxiliary_oppose': aux_oppose,
                    'vol_scale': round(vol_scale, 3),
                    'consistency_boost': round(consistency_boost, 3),
                }
            )
            
            self.logger.info(f"组合信号生成: {symbol} - {signal_type.value}, 置信度: {final_confidence:.3f}, "
                           f"买入: {buy_strength:.3f}, 卖出: {sell_strength:.3f}, 持有: {hold_strength:.3f}, "
                           f"触发: {trigger_sources}, 辅助同意: {aux_agree}, 辅助反对: {aux_oppose}, "
                           f"vol_scale={vol_scale:.2f}, consistency×{consistency_boost:.2f}")
            
            return ensemble_signal
            
        except Exception as e:
            self.logger.error(f"信号组合失败: {e}")
            return Signal(
                symbol=symbol,
                signal_type=SignalType.HOLD,
                price=data.get('last_done', 0),
                confidence=0.0,
                quantity=0,
                strategy_name="ensemble_error"
            )
            
    def _get_vol_scale(self, symbol: str, data: Dict[str, Any]) -> float:
        """P1-8: 基于近期价格波动率（默认日 K %变化标准差）返回仓位缩放系数。
        
        scale = clip(target_vol / actual_vol, scale_min, scale_max)
        - 高波动 → scale < 1.0 → 少配
        - 低波动 → scale > 1.0 → 多配（仍受 fund_guard 单标的上限保护）
        """
        if not self._vol_enabled:
            return 1.0
        try:
            actual_vol = self._estimate_volatility(symbol, data)
            if not actual_vol or actual_vol <= 0:
                return 1.0
            scale = self._vol_target_pct / actual_vol
            return float(max(self._vol_scale_min, min(self._vol_scale_max, scale)))
        except Exception as e:
            self.logger.debug(f"vol_scale 计算失败 {symbol}: {e}")
            return 1.0
    
    def _estimate_volatility(self, symbol: str, data: Dict[str, Any]) -> float:
        """估算近期波动率（日%标准差），优先级：
        1. 内部缓存（4h TTL）
        2. data 中的 'recent_returns' / 'recent_closes'
        3. 缺失则返回 0（上层视为不调整）
        """
        from datetime import timedelta as _td
        now = datetime.now()
        cached = self._vol_cache.get(symbol)
        if cached and (now - cached[0]) < _td(hours=4):
            return cached[1]
        
        try:
            recent_closes = data.get('recent_closes') if isinstance(data, dict) else None
            recent_returns = data.get('recent_returns') if isinstance(data, dict) else None
            if recent_returns is not None and len(recent_returns) >= 5:
                arr = np.asarray(recent_returns, dtype=float)
                vol = float(np.nanstd(arr) * 100)
            elif recent_closes is not None and len(recent_closes) >= 6:
                arr = np.asarray(recent_closes, dtype=float)
                rets = np.diff(arr) / arr[:-1]
                vol = float(np.nanstd(rets) * 100)
            else:
                # 触发后台异步刷新（基于 historical_loader，由 order_manager 持有）
                self._schedule_vol_refresh(symbol)
                return 0.0
            self._vol_cache[symbol] = (now, vol)
            return vol
        except Exception as e:
            self.logger.debug(f"波动率估算失败 {symbol}: {e}")
            return 0.0
    
    def _schedule_vol_refresh(self, symbol: str):
        """异步刷新波动率缓存（依赖 historical_loader 通过 order_manager 暴露）。"""
        loader = None
        try:
            if self._order_manager and hasattr(self._order_manager, 'historical_loader'):
                loader = getattr(self._order_manager, 'historical_loader', None)
        except Exception:
            loader = None
        if loader is None:
            return
        try:
            asyncio.create_task(self._refresh_vol_cache(symbol, loader))
        except RuntimeError:
            pass
    
    async def _refresh_vol_cache(self, symbol: str, loader):
        try:
            df = await loader.get_candlesticks(symbol, count=25, use_cache=True)
            if df is None or df.empty or 'close' not in df.columns or len(df) < 6:
                return
            closes = df['close'].values.astype(float)[-21:]
            rets = np.diff(closes) / closes[:-1]
            vol = float(np.nanstd(rets) * 100)
            self._vol_cache[symbol] = (datetime.now(), vol)
            self.logger.debug(f"波动率缓存刷新: {symbol} = {vol:.2f}%")
        except Exception as e:
            self.logger.debug(f"刷新波动率缓存失败 {symbol}: {e}")
    
    def _record_signal_history(self, strategy_signals: Dict[str, Signal], ensemble_signal: Signal):
        """记录信号历史"""
        history_entry = {
            'timestamp': datetime.now(),
            'symbol': ensemble_signal.symbol,
            'ensemble_signal': ensemble_signal.to_dict(),
            'strategy_signals': {k: v.to_dict() for k, v in strategy_signals.items()},
            'weights': self.strategy_weights.copy()
        }
        
        self.signal_history.append(history_entry)
        
        # 保持历史长度限制
        if len(self.signal_history) > self.max_history_length:
            self.signal_history = self.signal_history[-self.max_history_length:]
            
    async def _reweight_strategies(self):
        """重新计算策略权重"""
        try:
            if self.ensemble_method == EnsembleMethod.EQUAL_WEIGHT:
                return  # 等权重不需要重新计算
                
            self.logger.info("开始重新计算策略权重...")
            
            # 更新策略性能
            await self._update_strategy_performance()
            
            # 根据方法重新计算权重
            if self.ensemble_method == EnsembleMethod.PERFORMANCE_WEIGHT:
                self._reweight_by_performance()
            elif self.ensemble_method == EnsembleMethod.DYNAMIC_WEIGHT:
                self._reweight_dynamically()
                
            # 权重归一化
            self._normalize_weights()
            
            self.logger.info(f"策略权重更新完成: {self.strategy_weights}")
            
        except Exception as e:
            self.logger.error(f"重新计算权重失败: {e}")
            
    def _maybe_refresh_weights_from_pnl(self):
        """P2-15: 每日聚合 orders.csv 中各 signal_source 胜率/盈亏比，
        以基线权重为锚做平滑加权调整。"""
        if not self._weight_feedback_enabled or not self.strategy_weights:
            return
        now = datetime.now()
        if (self._weight_feedback_last_run is not None
                and (now - self._weight_feedback_last_run) < self._weight_feedback_interval):
            return
        self._weight_feedback_last_run = now
        try:
            from analytics.pnl_analytics import compute_strategy_winrates
            stats_map = compute_strategy_winrates(
                self._weight_feedback_orders_csv,
                lookback_days=self._weight_feedback_lookback_days,
                logger=self.logger,
            )
        except Exception as e:
            self.logger.debug(f"权重 PnL 反馈刷新失败: {e}")
            return
        
        if not stats_map:
            return
        
        # 将 signal_source（可能是 "volume,sec" 复合）映射回单一策略
        strategy_score: Dict[str, float] = {}
        strategy_count: Dict[str, int] = {}
        for source, stats in stats_map.items():
            if stats.trades < self._weight_feedback_min_trades:
                continue
            # source 形如 "volume_anomaly" 或 "ccass" 或 "volume_anomaly,sec"
            parts = [p.strip() for p in source.split(",") if p.strip()]
            score = max(0.05, stats.win_rate * max(0.1, stats.payoff_ratio))
            for p in parts:
                # 关键词匹配到内部 strategy_name
                matched = self._match_strategy_name(p)
                if not matched:
                    continue
                strategy_score[matched] = strategy_score.get(matched, 0.0) + score
                strategy_count[matched] = strategy_count.get(matched, 0) + 1
        
        if not strategy_score:
            self.logger.debug("权重 PnL 反馈：无足够样本可参考，保持当前权重")
            return
        
        # 计算每个策略的归一化得分（无样本的策略保持基线）
        for name in strategy_score:
            strategy_score[name] /= max(1, strategy_count[name])
        
        # EWMA 平滑：new = (1-α) * baseline + α * (baseline * normalized_score / mean_score)
        scores = list(strategy_score.values())
        mean_score = sum(scores) / len(scores) if scores else 1.0
        if mean_score <= 0:
            return
        
        new_weights = dict(self.strategy_weights)
        alpha = self._weight_feedback_alpha
        for name, base_w in self._weight_baseline.items():
            if name in strategy_score:
                rel = strategy_score[name] / mean_score
                target = base_w * rel
                new_weights[name] = (1 - alpha) * base_w + alpha * target
            # 无样本者保持基线（已经在 new_weights）
        
        # 归一化
        total = sum(new_weights.values())
        if total <= 0:
            return
        for k in new_weights:
            new_weights[k] /= total
        
        # 仅在显著差异时记录
        max_diff = max(abs(new_weights[k] - self.strategy_weights.get(k, 0)) for k in new_weights)
        if max_diff > 0.01:
            self.logger.info(
                f"📊 权重 PnL 反馈生效: {{ {', '.join(f'{k}: {self.strategy_weights.get(k, 0):.2f}→{v:.2f}' for k, v in new_weights.items())} }}"
            )
        self.strategy_weights = new_weights
    
    def _match_strategy_name(self, source_token: str) -> Optional[str]:
        """将 orders.csv 中的 signal_source 关键字（如 'volume', 'ccass'）映射回内部 strategy_name。"""
        if not source_token:
            return None
        token = source_token.lower()
        # 直接命中
        if token in self.strategy_weights:
            return token
        # 关键词匹配
        for name in self.strategy_weights.keys():
            n = name.lower()
            if token in n or n in token:
                return name
        return None
    
    async def _update_strategy_performance(self):
        """更新策略性能指标"""
        # 这里可以集成更复杂的性能计算逻辑
        # 暂时使用简化版本，基于最近的信号准确率
        
        recent_signals = self.signal_history[-100:]  # 最近100个信号
        
        for strategy_name in self.strategies.keys():
            strategy_history = [
                entry for entry in recent_signals 
                if strategy_name in entry['strategy_signals']
            ]
            
            if len(strategy_history) > 10:  # 至少需要10个样本
                # 简化的准确率计算（这里需要实际的交易结果数据）
                # 暂时基于信号一致性作为代理指标
                accuracy = self._calculate_signal_consistency(strategy_name, strategy_history)
                self.performance_tracker[strategy_name].recent_accuracy = accuracy
                
    def _calculate_signal_consistency(self, strategy_name: str, history: List[Dict]) -> float:
        """计算信号一致性（作为性能代理指标）"""
        try:
            if len(history) < 2:
                return 0.5  # 默认值
                
            consistent_count = 0
            total_count = len(history) - 1
            
            for i in range(1, len(history)):
                prev_signal = history[i-1]['strategy_signals'].get(strategy_name)
                curr_signal = history[i]['strategy_signals'].get(strategy_name)
                
                if prev_signal and curr_signal:
                    # 如果信号方向一致且置信度相近，认为是一致的
                    if (prev_signal['signal_type'] == curr_signal['signal_type'] and
                        abs(prev_signal['confidence'] - curr_signal['confidence']) < 0.3):
                        consistent_count += 1
                        
            return consistent_count / total_count if total_count > 0 else 0.5
            
        except Exception as e:
            self.logger.error(f"计算信号一致性失败: {e}")
            return 0.5
            
    def _reweight_by_performance(self):
        """基于性能重新分配权重"""
        performance_scores = {}
        
        for strategy_name, perf in self.performance_tracker.items():
            # 综合评分（可以根据需要调整权重）
            score = (
                perf.recent_accuracy * 0.4 +
                max(0, perf.win_rate) * 0.3 +
                max(0, perf.sharpe_ratio / 3.0) * 0.2 +  # 归一化夏普比率
                max(0, 1 - abs(perf.max_drawdown)) * 0.1
            )
            performance_scores[strategy_name] = max(0.1, score)  # 最低权重0.1
            
        # 根据评分分配权重
        total_score = sum(performance_scores.values())
        for strategy_name in self.strategies.keys():
            self.strategy_weights[strategy_name] = performance_scores[strategy_name] / total_score
            
    def _reweight_dynamically(self):
        """动态权重调整"""
        # 基于最近表现和市场条件动态调整
        # 这里可以实现更复杂的动态权重算法
        
        # 简化版本：基于最近准确率调整
        accuracy_scores = {}
        for strategy_name, perf in self.performance_tracker.items():
            accuracy_scores[strategy_name] = max(0.1, perf.recent_accuracy)
            
        total_accuracy = sum(accuracy_scores.values())
        for strategy_name in self.strategies.keys():
            self.strategy_weights[strategy_name] = accuracy_scores[strategy_name] / total_accuracy
            
    def _normalize_weights(self):
        """权重归一化"""
        total_weight = sum(self.strategy_weights.values())
        if total_weight > 0:
            for strategy_name in self.strategy_weights.keys():
                self.strategy_weights[strategy_name] /= total_weight
                
    def get_strategy_performance_summary(self) -> Dict[str, Any]:
        """获取策略性能摘要"""
        summary = {
            'total_signals': self.signal_count,
            'strategy_weights': self.strategy_weights.copy(),
            'performance_metrics': {},
            'recent_signals': len(self.signal_history)
        }
        
        for strategy_name, perf in self.performance_tracker.items():
            summary['performance_metrics'][strategy_name] = {
                'recent_accuracy': perf.recent_accuracy,
                'win_rate': perf.win_rate,
                'sharpe_ratio': perf.sharpe_ratio,
                'max_drawdown': perf.max_drawdown,
                'signal_count': perf.signal_count
            }
            
        return summary
        
    def update_strategy_performance_from_trades(self, trade_results: List[Dict]):
        """
        基于实际交易结果更新策略性能
        
        Args:
            trade_results: 交易结果列表，每个元素包含:
                - strategy_name: 策略名称
                - symbol: 股票代码
                - signal_type: 信号类型 (BUY/SELL)
                - entry_price: 入场价格
                - exit_price: 出场价格 (可选)
                - quantity: 数量
                - realized_pnl: 已实现盈亏 (可选)
                - investment: 投资金额
                - timestamp: 交易时间
        """
        try:
            if not trade_results:
                self.logger.debug("没有交易结果可用于更新策略性能")
                return
            
            self.logger.info(f"开始基于 {len(trade_results)} 笔交易更新策略性能...")
            
            # 按策略分组交易结果
            from collections import defaultdict
            strategy_trades = defaultdict(list)
            
            for trade in trade_results:
                strategy_name = trade.get('strategy_name', 'unknown')
                # 处理组合策略的名称
                if strategy_name.startswith('ensemble_'):
                    strategy_name = strategy_name.replace('ensemble_', '')
                elif strategy_name == 'ensemble':
                    # 从extra_data中获取贡献策略
                    contributing = trade.get('contributing_strategies', [])
                    for s in contributing:
                        strategy_trades[s].append(trade)
                    continue
                    
                strategy_trades[strategy_name].append(trade)
            
            # 更新每个策略的性能指标
            for strategy_name, trades in strategy_trades.items():
                if strategy_name not in self.performance_tracker:
                    continue
                    
                perf = self.performance_tracker[strategy_name]
                
                # 计算盈亏和收益率
                returns = []
                wins = 0
                total_trades = len(trades)
                
                for trade in trades:
                    pnl = trade.get('realized_pnl', 0)
                    investment = trade.get('investment', 1)
                    
                    if investment > 0:
                        return_rate = pnl / investment
                        returns.append(return_rate)
                        
                        if pnl > 0:
                            wins += 1
                
                if total_trades > 0 and returns:
                    # 更新胜率
                    perf.win_rate = wins / total_trades
                    
                    # 更新总收益
                    perf.total_return = sum(returns)
                    
                    # 更新夏普比率 (简化版)
                    avg_return = np.mean(returns)
                    std_return = np.std(returns) if len(returns) > 1 else 0.0001
                    perf.sharpe_ratio = avg_return / (std_return + 0.0001)
                    
                    # 更新最大回撤 (简化版)
                    cumulative = np.cumsum(returns)
                    running_max = np.maximum.accumulate(cumulative)
                    drawdowns = running_max - cumulative
                    perf.max_drawdown = np.max(drawdowns) if len(drawdowns) > 0 else 0
                    
                    # 更新信号数量
                    perf.signal_count = total_trades
                    perf.last_update = datetime.now()
                    
                    self.logger.info(f"策略 {strategy_name} 性能更新: "
                                   f"胜率={perf.win_rate:.2%}, "
                                   f"夏普={perf.sharpe_ratio:.2f}, "
                                   f"最大回撤={perf.max_drawdown:.2%}, "
                                   f"交易数={total_trades}")
            
            # 重新计算权重
            if self.ensemble_method != EnsembleMethod.EQUAL_WEIGHT:
                self._reweight_by_performance()
                self._normalize_weights()
                self.logger.info(f"策略权重已更新: {self.strategy_weights}")
                
        except Exception as e:
            self.logger.error(f"更新策略性能失败: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
    
    async def update_weights_from_order_history(self, order_manager):
        """
        从订单历史自动更新策略权重
        
        Args:
            order_manager: 订单管理器实例
        """
        try:
            # 获取已成交订单
            filled_orders = await order_manager.get_filled_orders()
            
            if not filled_orders:
                self.logger.debug("没有已成交订单可用于更新权重")
                return
            
            # 转换订单为交易结果格式
            trade_results = []
            for order in filled_orders:
                trade_result = {
                    'strategy_name': getattr(order, 'strategy_name', 'unknown'),
                    'symbol': order.symbol,
                    'signal_type': str(order.side),
                    'entry_price': float(order.price),
                    'quantity': int(order.quantity),
                    'investment': float(order.price) * int(order.quantity),
                    'realized_pnl': getattr(order, 'realized_pnl', 0),
                    'timestamp': getattr(order, 'created_at', datetime.now())
                }
                trade_results.append(trade_result)
            
            self.update_strategy_performance_from_trades(trade_results)
            
        except Exception as e:
            self.logger.error(f"从订单历史更新权重失败: {e}")