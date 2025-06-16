#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Union, Any, Callable
from enum import Enum
import time
import json
import logging
import os
import traceback
import tensorflow as tf

from utils import ConfigLoader, setup_logger
from data_loader.realtime import RealtimeDataManager
from data_loader.historical import HistoricalDataLoader
from strategy.train import LSTMModelTrainer
from longport.openapi import SubType

class SignalType(Enum):
    """交易信号类型"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    UNKNOWN = "UNKNOWN"

class Signal:
    """交易信号类"""
    
    def __init__(
        self, 
        symbol: str, 
        signal_type: SignalType, 
        price: float, 
        confidence: float = 0.0,
        quantity: int = 0,
        extra_data: Dict[str, Any] = None,
        strategy_name: str = "default"
    ):
        """
        初始化交易信号
        
        Args:
            symbol: 股票代码
            signal_type: 信号类型
            price: 信号价格
            confidence: 信号置信度，0-1之间
            quantity: 建议交易数量
            extra_data: 额外数据
            strategy_name: 策略名称
        """
        self.symbol = symbol
        self.signal_type = signal_type
        self.price = price
        self.confidence = confidence
        self.quantity = quantity
        self.extra_data = extra_data or {}
        self.strategy_name = strategy_name
        self.created_at = datetime.now()
        # 生成唯一ID
        self.id = f"{symbol}_{signal_type.value}_{self.created_at.strftime('%Y%m%d%H%M%S%f')}"
        
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'id': self.id,
            "symbol": self.symbol,
            "signal_type": self.signal_type.value,
            "price": self.price,
            "confidence": self.confidence,
            "quantity": self.quantity,
            "extra_data": self.extra_data,
            "strategy_name": self.strategy_name,
            "created_at": self.created_at.isoformat()
        }
        
    def __str__(self) -> str:
        """转换为字符串"""
        return (
            f"Signal({self.symbol}, {self.signal_type.value}, "
            f"price={self.price:.2f}, confidence={self.confidence:.2f}, "
            f"quantity={self.quantity})"
        )

class SignalGenerator:
    """信号生成器"""
    
    def __init__(self, config, realtime_mgr, model_trainer, portfolio_manager=None):
        """
        初始化信号生成器
        
        Args:
            config: 配置字典
            realtime_mgr: 实时数据管理器
            model_trainer: LSTM模型训练器
            portfolio_manager: 投资组合管理器（可选）
        """
        self.config = config
        self.realtime_mgr = realtime_mgr
        self.model_trainer = model_trainer
        self.portfolio_manager = portfolio_manager
        self.model = None
        self.data_cache = {}
        self.callbacks = []
        self.logger = logging.getLogger(__name__)
        
        # 初始化信号生成间隔
        self.signal_interval = config.get("strategy.signal_interval", 300)  # 默认300秒(5分钟)
        
        # 初始化回看周期
        self.lookback_period = config.get("strategy.lookback_period", 30)  # 默认30个数据点
        
        # 添加时间控制机制，避免过于频繁的信号生成
        self.last_signal_time = {}  # 记录每个股票最后生成信号的时间
        self.min_signal_interval = 30  # 每个股票最少30秒间隔才能生成新信号
        
    async def start(self, symbols=None):
        """启动信号生成器"""
        try:
            self.logger.info("正在加载模型...")
            self.model = self.model_trainer.load_model()
            if self.model is None:
                raise ValueError("无法加载模型")
            
            self.logger.info("模型加载成功")
            
            # 如果提供了股票列表，预填充历史数据
            if symbols:
                await self._prefill_historical_data(symbols)
            
            # 注册实时数据回调，指定事件类型为 "Quote"
            self.realtime_mgr.register_callback("Quote", self.update_data)
            
            self.logger.info("信号生成器启动完成")
            return True
        except Exception as e:
            self.logger.error(f"启动信号生成器时出错: {str(e)}")
            return False
    
    async def _prefill_historical_data(self, symbols):
        """使用历史数据预填充缓存"""
        try:
            self.logger.info(f"开始预填充历史数据，股票: {symbols}")
            
            for symbol in symbols:
                try:
                    self.logger.info(f"获取 {symbol} 的历史数据...")
                    
                    # 获取历史K线数据
                    hist_data = await self.model_trainer.data_loader.get_candlesticks(
                        symbol, 
                        count=self.lookback_period + 10  # 多获取一些数据以确保有足够的样本
                    )
                    
                    if hist_data.empty:
                        self.logger.warning(f"无法获取 {symbol} 的历史数据")
                        continue
                    
                    # 转换历史数据到缓存格式
                    self.data_cache[symbol] = []
                    
                    # 取最近的数据点
                    recent_data = hist_data.tail(self.lookback_period)
                    
                    for _, row in recent_data.iterrows():
                        data_point = {
                            "last_done": float(row['close']),
                            "open": float(row['open']),
                            "high": float(row['high']),
                            "low": float(row['low']),
                            "volume": float(row['volume']),
                            "timestamp": row['datetime'] if 'datetime' in row else datetime.now()
                        }
                        self.data_cache[symbol].append(data_point)
                    
                    self.logger.info(f"成功预填充 {symbol} 历史数据，共 {len(self.data_cache[symbol])} 条记录")
                    
                except Exception as e:
                    self.logger.error(f"预填充 {symbol} 历史数据失败: {e}")
                    
        except Exception as e:
            self.logger.error(f"预填充历史数据失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
        
    def register_callback(self, callback: Callable):
        """注册回调函数"""
        self.callbacks.append(callback)
        
    async def update_data(self, symbol: str, quote: Any):
        """更新数据并生成信号
        
        Args:
            symbol: 股票代码
            quote: 行情数据
        """
        try:
            self.logger.debug(f"收到行情数据: {symbol}, 价格: {quote.last_done}")
            
            # 检查时间限制，避免过于频繁的信号生成
            current_time = datetime.now()
            if symbol in self.last_signal_time:
                time_diff = (current_time - self.last_signal_time[symbol]).total_seconds()
                if time_diff < self.min_signal_interval:
                    self.logger.debug(f"信号生成过于频繁，跳过: {symbol}, 距离上次生成仅 {time_diff:.1f} 秒")
                    return
            
            # 转换行情数据为字典格式
            data = {
                "last_done": quote.last_done,
                "open": quote.open,
                "high": quote.high,
                "low": quote.low,
                "volume": quote.volume,
                "timestamp": current_time
            }
            
            # 更新数据缓存
            if symbol not in self.data_cache:
                self.data_cache[symbol] = []
                
            self.data_cache[symbol].append(data)
            
            # 保持缓存大小
            if len(self.data_cache[symbol]) > self.lookback_period:
                self.data_cache[symbol] = self.data_cache[symbol][-self.lookback_period:]
                
            # 如果数据足够，生成信号
            if len(self.data_cache[symbol]) >= self.lookback_period:
                self.logger.info(f"数据缓存已满，生成信号: {symbol}")
                
                # 准备模型输入数据
                input_data = self._prepare_model_input(symbol)
                
                # 使用模型预测
                prediction = self.model.predict(input_data, verbose=0)[0][0]
                
                self.logger.info(f"模型预测结果: {prediction}")
                
                # 生成信号
                signal = self._generate_signal(symbol, prediction, data)
                
                # 更新最后信号时间
                self.last_signal_time[symbol] = current_time
                
                # 通知回调函数
                for callback in self.callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(signal)
                        else:
                            callback(signal)
                    except Exception as e:
                        self.logger.error(f"执行回调函数失败: {str(e)}")
            else:
                self.logger.debug(f"数据缓存不足，当前缓存大小: {len(self.data_cache[symbol])}/{self.lookback_period}")
                        
        except Exception as e:
            self.logger.error(f"更新数据失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            
    def _prepare_model_input(self, symbol: str) -> np.ndarray:
        """准备模型输入数据"""
        try:
            # 获取最近的数据
            recent_data = self.data_cache[symbol][-self.lookback_period:]
            
            # 提取特征
            features = []
            for data in recent_data:
                features.append([
                    data["last_done"],
                    data["open"],
                    data["high"],
                    data["low"],
                    data["volume"]
                ])
                
            # 转换为numpy数组并标准化
            features = np.array(features, dtype=np.float64)
            
            # 计算均值和标准差，确保数据类型兼容性
            mean_vals = np.mean(features, axis=0)
            std_vals = np.std(features, axis=0)
            
            # 避免除零错误，同时确保数据类型兼容性
            std_vals = np.where(std_vals == 0, 1e-8, std_vals)
            
            # 标准化特征
            features = (features - mean_vals) / std_vals
            
            # 添加批次维度
            features = np.expand_dims(features, axis=0)
            
            return features
            
        except Exception as e:
            self.logger.error(f"准备模型输入数据失败: {str(e)}")
            raise
            
    def _generate_signal(self, symbol: str, prediction: float, latest_data: Dict[str, Any]) -> Signal:
        """生成交易信号"""
        try:
            # 详细记录预测值
            self.logger.info(f"模型预测结果详情: {symbol}, 预测值: {prediction:.6f}")
            
            # 从配置文件获取信号阈值
            buy_threshold = self.config.get("strategy.signal_processing.buy_threshold", 0.08)
            sell_threshold = self.config.get("strategy.signal_processing.sell_threshold", -0.08)
            
            # 根据预测结果确定信号类型
            if prediction > buy_threshold:
                signal_type = SignalType.BUY
                self.logger.info(f"生成买入信号: {symbol}, 预测值 {prediction:.6f} > 阈值 {buy_threshold}")
            elif prediction < sell_threshold:
                signal_type = SignalType.SELL
                self.logger.info(f"生成卖出信号: {symbol}, 预测值 {prediction:.6f} < 阈值 {sell_threshold}")
            else:
                signal_type = SignalType.HOLD
                self.logger.info(f"生成持有信号: {symbol}, 预测值 {prediction:.6f} 在 ±{buy_threshold} 范围内")
                
            # 计算置信度
            confidence = abs(prediction)
            
            # 计算建议交易数量
            quantity = self._calculate_quantity(symbol, latest_data)
            
            # 创建信号对象
            signal = Signal(
                symbol=symbol,
                signal_type=signal_type,
                price=latest_data["last_done"],
                confidence=confidence,
                quantity=quantity,
                extra_data=latest_data
            )
            
            self.logger.info(f"生成信号: {signal}")
            return signal
            
        except Exception as e:
            self.logger.error(f"生成信号失败: {str(e)}")
            raise
            
    def _calculate_quantity(self, symbol: str, data: Dict[str, Any]) -> int:
        """计算建议交易数量"""
        try:
            # 如果有投资组合管理器，使用其建议
            if self.portfolio_manager:
                try:
                    # 获取预测置信度
                    confidence = abs(data.get("confidence", 0.1))
                    
                    # 从投资组合管理器获取建议
                    action, portfolio_quantity = self.portfolio_manager.get_position_suggestion(symbol, confidence)
                    
                    if portfolio_quantity > 0:
                        self.logger.info(f"投资组合管理器建议: {symbol}, 动作={action}, 数量={portfolio_quantity}, 置信度={confidence}")
                        return portfolio_quantity
                    else:
                        self.logger.debug(f"投资组合管理器建议: {symbol}, 持有不变")
                        
                except Exception as e:
                    self.logger.warning(f"获取投资组合建议失败，使用传统计算方法: {e}")
            
            # 传统计算方法（备用）
            max_position_value = self.config.get("execution.max_position_size", 10000)
            current_price = float(data["last_done"])
            confidence = min(abs(data.get("confidence", 0.1)), 1.0)
            position_ratio = max(0.1, confidence)  # 至少使用10%的可用资金
            
            target_value = max_position_value * position_ratio
            raw_quantity = target_value / current_price
            quantity = max(1, int(raw_quantity))
            
            self.logger.info(f"传统方法计算数量: 符号={symbol}, 价格={current_price}, 置信度={confidence}, " +
                            f"目标金额={target_value}, 原始数量={raw_quantity}, 最终数量={quantity}")
            
            return quantity
            
        except Exception as e:
            self.logger.error(f"计算交易数量失败: {str(e)}")
            return 1
            
    async def scheduled_signal_generation(self, interval_seconds: int = 60):
        """定时生成交易信号
        
        Args:
            interval_seconds: 信号生成间隔，默认60秒
        """
        try:
            # 加载模型
            if self.model is None:
                await self.start()
                
            self.logger.info(f"启动定时信号生成，间隔 {interval_seconds} 秒")
            
            iteration = 0
            while True:
                try:
                    iteration += 1
                    # 记录开始生成信号的时间
                    start_time = datetime.now()
                    self.logger.info(f"开始第 {iteration} 轮定时信号生成，时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    # 获取最新的行情数据
                    symbols = list(self.data_cache.keys())
                    if not symbols:
                        self.logger.warning("没有可用的数据缓存，等待数据")
                        await asyncio.sleep(interval_seconds)
                        continue
                    
                    self.logger.info(f"当前缓存包含以下股票数据: {symbols}")
                    
                    signals_generated = 0
                    for symbol in symbols:
                        try:
                            # 如果数据不足，跳过
                            if symbol not in self.data_cache or len(self.data_cache[symbol]) < self.lookback_period:
                                self.logger.warning(f"{symbol} 数据不足，跳过信号生成。当前数据量: {len(self.data_cache.get(symbol, []))}/{self.lookback_period}")
                                continue
                                
                            # 获取最新价格
                            latest_data = self.data_cache[symbol][-1]
                            self.logger.info(f"最新行情数据: {symbol}, 价格: {latest_data['last_done']}, 时间: {latest_data['timestamp']}")
                            
                            # 准备模型输入
                            self.logger.debug(f"准备 {symbol} 的模型输入数据...")
                            input_data = self._prepare_model_input(symbol)
                            
                            # 使用模型预测
                            self.logger.debug(f"使用LSTM模型预测 {symbol} 的价格变动...")
                            raw_prediction = self.model.predict(input_data, verbose=0)
                            prediction = raw_prediction[0][0]
                            
                            self.logger.info(f"模型预测结果: {symbol}, 预测值: {prediction:.6f}")
                            
                            # 生成信号
                            signal = self._generate_signal(symbol, prediction, latest_data)
                            
                            # 记录信号详情
                            if signal.signal_type != SignalType.HOLD:
                                self.logger.info(f"生成非持仓信号! 详情: {signal}")
                            
                            # 触发回调
                            for callback in self.callbacks:
                                self.logger.debug(f"调用回调函数处理信号: {signal}")
                                if asyncio.iscoroutinefunction(callback):
                                    await callback(signal)
                                else:
                                    callback(signal)
                            
                            signals_generated += 1
                            
                        except Exception as e:
                            self.logger.error(f"处理 {symbol} 信号生成时出错: {e}")
                            import traceback
                            self.logger.error(traceback.format_exc())
                    
                    # 记录本次信号生成耗时
                    end_time = datetime.now()
                    elapsed = (end_time - start_time).total_seconds()
                    self.logger.info(f"完成第 {iteration} 轮信号生成，处理 {signals_generated} 个股票，耗时: {elapsed:.2f}秒")
                                
                except Exception as e:
                    self.logger.error(f"第 {iteration} 轮定时生成信号失败: {str(e)}")
                    import traceback
                    self.logger.error(traceback.format_exc())
                
                # 等待下一次信号生成
                self.logger.info(f"等待 {interval_seconds} 秒进行第 {iteration + 1} 轮信号生成...")
                await asyncio.sleep(interval_seconds)
                
        except asyncio.CancelledError:
            self.logger.info("定时信号生成任务被取消")
            raise
        except Exception as e:
            self.logger.error(f"定时任务严重异常: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())
            raise
