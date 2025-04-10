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
    
    def __init__(self, config, realtime_mgr, model_trainer):
        """
        初始化信号生成器
        
        Args:
            config: 配置字典
            realtime_mgr: 实时数据管理器
            model_trainer: LSTM模型训练器
        """
        self.config = config
        self.realtime_mgr = realtime_mgr
        self.model_trainer = model_trainer
        self.model = None
        self.data_cache = {}
        self.callbacks = []
        self.logger = logging.getLogger(__name__)
        
        # 初始化信号生成间隔
        self.signal_interval = config.get("strategy.signal_interval", 60)  # 默认60秒
        
        # 初始化回看周期
        self.lookback_period = config.get("strategy.lookback_period", 30)  # 默认30个数据点
        
    async def start(self):
        """启动信号生成器"""
        try:
            self.logger.info("正在加载模型...")
            self.model = self.model_trainer.load_model()
            if self.model is None:
                raise ValueError("无法加载模型")
            
            self.logger.info("模型加载成功")
            
            # 注册实时数据回调，指定事件类型为 "Quote"
            self.realtime_mgr.register_callback("Quote", self.update_data)
            
            self.logger.info("信号生成器启动完成")
            return True
        except Exception as e:
            self.logger.error(f"启动信号生成器时出错: {str(e)}")
            return False
        
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
            # 转换行情数据为字典格式
            data = {
                "last_done": quote.last_done,
                "open": quote.open,
                "high": quote.high,
                "low": quote.low,
                "volume": quote.volume,
                "timestamp": datetime.now()
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
                # 准备模型输入数据
                input_data = self._prepare_model_input(symbol)
                
                # 使用模型预测
                prediction = self.model.predict(input_data, verbose=0)[0][0]
                
                # 生成信号
                signal = self._generate_signal(symbol, prediction, data)
                
                # 通知回调函数
                for callback in self.callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(signal)
                        else:
                            callback(signal)
                    except Exception as e:
                        self.logger.error(f"执行回调函数失败: {str(e)}")
                        
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
            features = np.array(features)
            features = (features - np.mean(features, axis=0)) / (np.std(features, axis=0) + 1e-8)
            
            # 添加批次维度
            features = np.expand_dims(features, axis=0)
            
            return features
            
        except Exception as e:
            self.logger.error(f"准备模型输入数据失败: {str(e)}")
            raise
            
    def _generate_signal(self, symbol: str, prediction: float, latest_data: Dict[str, Any]) -> Signal:
        """生成交易信号"""
        try:
            # 根据预测结果确定信号类型
            if prediction > 0.5:
                signal_type = SignalType.BUY
            elif prediction < -0.5:
                signal_type = SignalType.SELL
            else:
                signal_type = SignalType.HOLD
                
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
            # 这里可以实现更复杂的仓位计算逻辑
            # 当前简单实现：固定交易100股
            return 100
            
        except Exception as e:
            self.logger.error(f"计算交易数量失败: {str(e)}")
            return 0
