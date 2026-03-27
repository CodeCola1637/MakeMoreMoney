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
    
    def __init__(self, config, strategies: Dict[str, Any], ensemble_method: EnsembleMethod = EnsembleMethod.CONFIDENCE_WEIGHT):
        """
        初始化策略组合器
        
        Args:
            config: 配置对象
            strategies: 策略字典 {strategy_name: strategy_instance}
            ensemble_method: 组合方法
        """
        self.config = config
        self.strategies = strategies
        self.ensemble_method = ensemble_method
        
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
        self.reweight_frequency = config.get("ensemble.reweight_frequency", 100)  # 每100个信号重新计算权重
        self.signal_count = 0
        
        # 🔧 新增：信号过滤器 - 防止重复信号和过度交易
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
            
            # 🔧 新增：信号过滤检查 - 防止重复信号和过度交易
            should_emit, filter_reason = self.signal_filter.should_emit_signal(ensemble_signal)
            if not should_emit:
                self.logger.info(f"信号被过滤: {symbol} - {filter_reason}")
                return None
            
            # 记录信号到过滤器（用于冷却期和统计）
            self.signal_filter.record_signal(ensemble_signal)
            
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
        """过滤有效信号"""
        valid_signals = {}
        
        for strategy_name, signal in strategy_signals.items():
            # 检查信号有效性 - 修改逻辑：允许所有置信度达标的信号（包括HOLD）
            if (signal.confidence >= self.confidence_threshold and signal.price > 0):
                valid_signals[strategy_name] = signal
                self.logger.debug(f"策略 {strategy_name} 有效信号: {signal.signal_type.value}, 置信度: {signal.confidence:.3f}")
            else:
                self.logger.debug(f"策略 {strategy_name} 信号被过滤: 置信度 {signal.confidence:.3f} < {self.confidence_threshold}")
                
        return valid_signals
        
    def _combine_signals(self, valid_signals: Dict[str, Signal], symbol: str, data: Dict[str, Any]) -> Signal:
        """组合多个信号（弃权策略的权重按比例分配给参与者）"""
        try:
            # 计算参与策略的归一化权重
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
            
            # 确定最终信号类型 - 考虑HOLD信号
            if buy_strength > sell_strength and buy_strength > hold_strength and buy_strength > 0:
                signal_type = SignalType.BUY
                final_confidence = buy_strength
            elif sell_strength > buy_strength and sell_strength > hold_strength and sell_strength > 0:
                signal_type = SignalType.SELL
                final_confidence = sell_strength
            else:
                signal_type = SignalType.HOLD
                final_confidence = max(hold_strength, 0.05)  # HOLD信号也保持一定置信度
                
            # 计算平均价格
            avg_price = np.mean(prices) if prices else data.get('last_done', 0)
            
            # 计算建议数量（基于总置信度）
            base_quantity = 100
            quantity_multiplier = min(total_confidence * 2, 3.0)  # 最多3倍基础数量
            suggested_quantity = int(base_quantity * quantity_multiplier)
            
            # 创建组合信号
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
                    'strategy_weights': {k: round(v, 3) for k, v in norm_weights.items()}
                }
            )
            
            self.logger.info(f"组合信号生成: {symbol} - {signal_type.value}, 置信度: {final_confidence:.3f}, "
                           f"买入: {buy_strength:.3f}, 卖出: {sell_strength:.3f}, 持有: {hold_strength:.3f}, "
                           f"参与策略: {list(valid_signals.keys())}")
            
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