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

from utils import ConfigLoader, setup_logger
from data_loader.realtime import RealtimeDataManager
from data_loader.historical import HistoricalDataLoader
from strategy.train import LSTMModelTrainer
from longport.openapi import PushQuote, SubType

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
    """交易信号生成器，负责根据模型预测生成交易信号"""
    
    def __init__(
        self, 
        config_loader: ConfigLoader, 
        realtime_manager: RealtimeDataManager,
        model_trainer: LSTMModelTrainer
    ):
        """
        初始化交易信号生成器
        
        Args:
            config_loader: 配置加载器
            realtime_manager: 实时数据管理器
            model_trainer: 模型训练器
        """
        self.config = config_loader
        self.realtime_manager = realtime_manager
        self.model_trainer = model_trainer
        self.logger = setup_logger(
            "signal_generator", 
            self.config.get("logging.level", "INFO"),
            self.config.get("logging.file")
        )
        
        # 信号回调
        self.signal_callbacks: List[Callable[[Signal], None]] = []
        
        # 信号缓存
        self.signals: Dict[str, Signal] = {}
        
        # 最后预测时间
        self.last_prediction_time: Dict[str, datetime] = {}
        
        # 信号阈值参数
        self.buy_threshold = 0.01  # 价格上涨超过1%发出买入信号
        self.sell_threshold = -0.01  # 价格下跌超过1%发出卖出信号
        
        # 注册行情回调
        self.realtime_manager.register_callback("Quote", self._on_quote_update)
        
    def register_signal_callback(self, callback: Callable[[Signal], None]):
        """
        注册信号回调函数
        
        Args:
            callback: 回调函数，接收Signal参数
        """
        self.signal_callbacks.append(callback)
        self.logger.debug(f"已注册信号回调函数: {callback.__name__}")
        
    async def initialize(self):
        """初始化，包括加载模型等操作"""
        try:
            # 订阅默认股票行情
            self.realtime_manager.batch_subscribe_default()
            
            # 加载模型
            self.model_trainer.train_model(force_retrain=False)
            
        except Exception as e:
            self.logger.error(f"初始化信号生成器失败: {e}")
            raise
    
    async def predict_and_generate_signal(self, symbol: str) -> Optional[Signal]:
        """
        为指定股票生成交易信号
        
        Args:
            symbol: 股票代码
            
        Returns:
            生成的交易信号，如果无法生成则返回None
        """
        # 防止短时间内重复预测
        now = datetime.now()
        if symbol in self.last_prediction_time:
            elapsed = (now - self.last_prediction_time[symbol]).total_seconds()
            if elapsed < 60:  # 至少间隔60秒
                self.logger.debug(f"跳过 {symbol} 的信号生成，距上次生成时间不足60秒")
                return None
                
        self.last_prediction_time[symbol] = now
        
        try:
            # 获取最新价格
            latest_price = None
            quote = self.realtime_manager.get_latest_quote(symbol)
            
            if quote:
                latest_price = quote.last_done
                self.logger.debug(f"{symbol} 最新价格: {latest_price}")
            else:
                # 实时获取价格
                quotes = await self.realtime_manager.get_quote([symbol])
                if quotes and symbol in quotes:
                    quote = quotes[symbol]
                    latest_price = quote.last_done
                    self.logger.debug(f"{symbol} 实时价格: {latest_price}")
                else:
                    self.logger.warning(f"无法获取 {symbol} 的价格信息")
                    return None
                    
            # 进行预测
            try:
                # 确保处理异步方法
                prediction_result = await self.model_trainer.predict_next(symbol)
                
                if isinstance(prediction_result, dict) and "error" in prediction_result:
                    self.logger.error(f"预测 {symbol} 失败: {prediction_result['error']}")
                    return None
                    
                # 假设返回预测结果包含百分比变化
                predicted_change_pct = prediction_result.get('predicted_change_pct', 0)
                self.logger.info(f"{symbol} 预测价格变化: {predicted_change_pct:.2f}%")
                
                # 根据预测结果生成信号
                signal_type = SignalType.HOLD
                confidence = abs(predicted_change_pct) / 5.0  # 归一化置信度
                confidence = min(max(confidence, 0.0), 1.0)  # 限制在0-1之间
                
                # 根据预测涨跌幅判断买卖信号
                buy_threshold = self.config.get("strategy.thresholds.buy", 0.5)
                sell_threshold = self.config.get("strategy.thresholds.sell", -0.5)
                
                if predicted_change_pct >= buy_threshold:
                    signal_type = SignalType.BUY
                elif predicted_change_pct <= sell_threshold:
                    signal_type = SignalType.SELL
                
                try:
                    # 确保数值类型正确
                    try:
                        # 确保是浮点数
                        position_pct = float(self.config.get("execution.risk_control.position_pct", 2.0))
                        max_position_size = float(self.config.get("execution.max_position_size", 10000))
                        confidence = float(confidence)
                        latest_price = float(latest_price) if latest_price else 0.0
                        
                        # 计算建议交易数量
                        suggested_value = max_position_size * (position_pct / 100) * confidence
                        quantity = int(suggested_value / latest_price) if latest_price > 0 else 0
                    except Exception as e:
                        self.logger.error(f"计算交易数量时出错: {e}")
                        quantity = 0
                    
                    if quantity <= 0 and signal_type != SignalType.HOLD:
                        quantity = 1  # 至少交易1股
                        
                    # 创建信号对象
                    if signal_type != SignalType.HOLD:
                        signal = Signal(
                            symbol=symbol,
                            signal_type=signal_type,  # 使用 SignalType 枚举值
                            price=latest_price,
                            confidence=confidence,
                            quantity=quantity,
                            extra_data={
                                "predicted_change_pct": predicted_change_pct,
                                "model": "LSTM"
                            },
                            strategy_name="default"
                        )
                        return signal
                    else:
                        self.logger.info(f"{symbol} 预测变化不足以触发交易信号")
                        return None
                except Exception as e:
                    self.logger.error(f"创建信号对象时出错: {e}")
                    import traceback
                    traceback.print_exc()
                    return None
                    
            except Exception as e:
                self.logger.error(f"生成 {symbol} 的交易信号时出错: {e}")
                import traceback
                traceback.print_exc()
                return None
                
        except Exception as e:
            self.logger.error(f"生成 {symbol} 的交易信号失败: {e}")
            return None
    
    async def generate_signals_for_all(self) -> Dict[str, Signal]:
        """
        为所有已订阅的股票生成交易信号
        
        Returns:
            生成的交易信号字典，key为股票代码，value为Signal对象
        """
        symbols = list(self.realtime_manager.subscribed_symbols)
        if not symbols:
            self.logger.warning("没有订阅的股票，无法生成信号")
            return {}
            
        new_signals = {}
        
        for symbol in symbols:
            try:
                signal = await self.predict_and_generate_signal(symbol)
                if signal:
                    self.logger.info(f"生成信号: {signal}")
                    self.signals[symbol] = signal
                    new_signals[symbol] = signal
                    
                    # 触发回调
                    for callback in self.signal_callbacks:
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(signal)
                            else:
                                callback(signal)
                        except Exception as e:
                            self.logger.error(f"执行信号回调失败: {e}")
            except Exception as e:
                self.logger.error(f"生成信号失败: {symbol}, {e}")
                
        return new_signals
    
    def _on_quote_update(self, symbol: str, quote: PushQuote):
        """
        行情更新回调函数
        
        Args:
            symbol: 股票代码
            quote: 行情数据
        """
        # 这里可以实现基于实时行情的信号生成逻辑
        # 对于复杂策略，可以在此调用预测函数
        pass
    
    def get_latest_signal(self, symbol: str) -> Optional[Signal]:
        """
        获取指定股票的最新信号
        
        Args:
            symbol: 股票代码
            
        Returns:
            最新信号，如果没有则返回None
        """
        return self.signals.get(symbol)
    
    def get_all_signals(self) -> Dict[str, Signal]:
        """
        获取所有信号
        
        Returns:
            信号字典
        """
        return self.signals
    
    def clear_signals(self):
        """清除所有信号"""
        self.signals.clear()
        self.logger.info("已清除所有信号")
        
    async def scheduled_signal_generation(self, interval_seconds: int = 300):
        """
        定时生成交易信号的任务
        
        Args:
            interval_seconds: 信号生成间隔，单位为秒
        """
        self.logger.info(f"启动定时信号生成任务，间隔 {interval_seconds} 秒")
        
        while True:
            try:
                self.logger.debug("执行定时信号生成")
                
                # 获取已订阅的股票代码
                symbols = list(self.realtime_manager.subscribed_symbols)
                if not symbols:
                    self.logger.warning("没有订阅的股票，无法生成信号")
                    await asyncio.sleep(interval_seconds)
                    continue
                
                self.logger.info(f"正在为 {symbols} 生成交易信号")
                
                # 为每个股票生成信号
                for symbol in symbols:
                    try:
                        # 获取最新价格
                        quote = self.realtime_manager.get_latest_quote(symbol)
                        if not quote:
                            self.logger.warning(f"无法获取 {symbol} 的最新行情，尝试请求实时数据")
                            quotes = await self.realtime_manager.get_quote([symbol])
                            if not quotes or symbol not in quotes:
                                self.logger.error(f"无法获取 {symbol} 的最新行情，跳过信号生成")
                                continue
                            quote = quotes[symbol]
                        
                        # 使用最新价格生成信号
                        latest_price = quote.last_done
                        self.logger.info(f"{symbol} 最新价格: {latest_price}")
                        
                        # 使用LSTM模型预测
                        self.logger.info(f"为 {symbol} 使用LSTM模型预测")
                        
                        # 预测并生成信号
                        signal = await self.predict_and_generate_signal(symbol)
                        
                        if signal:
                            self.logger.info(f"生成信号: {signal}")
                            self.signals[symbol] = signal
                            
                            # 触发回调
                            for callback in self.signal_callbacks:
                                try:
                                    if asyncio.iscoroutinefunction(callback):
                                        await callback(signal)
                                    else:
                                        callback(signal)
                                except Exception as e:
                                    self.logger.error(f"执行信号回调失败: {e}")
                        else:
                            self.logger.info(f"未生成 {symbol} 的交易信号")
                    except Exception as e:
                        self.logger.error(f"为 {symbol} 生成信号时出错: {e}")
                
                self.logger.debug("定时信号生成完成")
            except Exception as e:
                self.logger.error(f"定时信号生成任务出错: {e}")
            
            # 等待下一次执行
            await asyncio.sleep(interval_seconds)
