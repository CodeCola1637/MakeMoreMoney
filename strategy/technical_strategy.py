#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
技术指标策略
基于多种技术指标的交易策略，用于与LSTM策略组合
"""

import numpy as np
import pandas as pd
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from strategy.signals import Signal, SignalType
from utils import setup_logger


class TechnicalStrategy:
    """技术指标策略"""
    
    def __init__(self, config, realtime_mgr, historical_loader):
        """
        初始化技术指标策略
        
        Args:
            config: 配置对象
            realtime_mgr: 实时数据管理器
            historical_loader: 历史数据加载器
        """
        self.config = config
        self.realtime_mgr = realtime_mgr
        self.historical_loader = historical_loader
        
        # 设置日志
        self.logger = setup_logger(
            "technical_strategy",
            self.config.get("logging.level", "INFO"),
            self.config.get("logging.file")
        )
        
        # 技术指标参数
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_signal = 9
        
        self.bb_period = 20
        self.bb_std = 2
        
        self.sma_short = 10
        self.sma_long = 30
        
        # 信号阈值
        self.buy_threshold = config.get("strategy.signal_processing.buy_threshold", 0.04)
        self.sell_threshold = config.get("strategy.signal_processing.sell_threshold", -0.04)
        
        self.logger.info("技术指标策略初始化完成")
        
    async def generate_signal(self, symbol: str, data: Dict[str, Any]) -> Optional[Signal]:
        """
        生成基于技术指标的交易信号
        
        Args:
            symbol: 股票代码
            data: 实时数据
            
        Returns:
            交易信号或None
        """
        try:
            # 获取历史数据
            df = await self._get_historical_data(symbol)
            if df is None or len(df) < 50:  # 需要足够的历史数据
                self.logger.warning(f"历史数据不足，无法生成技术指标信号: {symbol}")
                return None
                
            # 计算技术指标
            indicators = self._calculate_indicators(df)
            
            # 生成信号
            signal_score, confidence = self._generate_signal_score(indicators, data)
            
            # 确定信号类型
            if signal_score > self.buy_threshold:
                signal_type = SignalType.BUY
            elif signal_score < self.sell_threshold:
                signal_type = SignalType.SELL
            else:
                signal_type = SignalType.HOLD
                
            # 创建信号对象
            signal = Signal(
                symbol=symbol,
                signal_type=signal_type,
                price=data.get('last_done', 0),
                confidence=confidence,
                quantity=self._calculate_quantity(confidence),
                strategy_name="technical",
                extra_data={
                    'signal_score': signal_score,
                    'indicators': indicators,
                    'timestamp': datetime.now().isoformat()
                }
            )
            
            if signal_type != SignalType.HOLD:
                self.logger.info(f"技术指标策略信号: {symbol} {signal_type.value}, "
                               f"得分: {signal_score:.4f}, 置信度: {confidence:.3f}")
            else:
                self.logger.debug(f"技术指标策略: {symbol} HOLD, 得分: {signal_score:.4f}")
                
            return signal
            
        except Exception as e:
            self.logger.error(f"生成技术指标信号失败: {symbol}, 错误: {e}")
            return None
            
    async def _get_historical_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取历史数据"""
        try:
            # 获取最近50天的日线数据
            df = await self.historical_loader.get_candlesticks(symbol, period="Day", count=50)
            if df is not None and not df.empty:
                return df
            else:
                self.logger.warning(f"无法获取历史数据: {symbol}")
                return None
        except Exception as e:
            self.logger.error(f"获取历史数据失败: {symbol}, 错误: {e}")
            return None
            
    def _calculate_indicators(self, df: pd.DataFrame) -> Dict[str, float]:
        """计算技术指标"""
        try:
            indicators = {}
            
            # 确保数据格式正确
            if 'close' not in df.columns:
                self.logger.error("历史数据缺少close列")
                return {}
                
            close_prices = df['close'].astype(float)
            high_prices = df['high'].astype(float) if 'high' in df.columns else close_prices
            low_prices = df['low'].astype(float) if 'low' in df.columns else close_prices
            volume = df['volume'].astype(float) if 'volume' in df.columns else pd.Series([1] * len(df))
            
            # 1. RSI指标
            rsi = self._calculate_rsi(close_prices, self.rsi_period)
            indicators['rsi'] = rsi.iloc[-1] if not rsi.empty else 50.0
            indicators['rsi_signal'] = self._rsi_signal(indicators['rsi'])
            
            # 2. MACD指标
            macd_line, macd_signal, macd_histogram = self._calculate_macd(close_prices)
            indicators['macd'] = macd_line.iloc[-1] if not macd_line.empty else 0.0
            indicators['macd_signal'] = macd_signal.iloc[-1] if not macd_signal.empty else 0.0
            indicators['macd_histogram'] = macd_histogram.iloc[-1] if not macd_histogram.empty else 0.0
            indicators['macd_trend'] = self._macd_signal(indicators['macd'], indicators['macd_signal'])
            
            # 3. 布林带
            bb_upper, bb_middle, bb_lower = self._calculate_bollinger_bands(close_prices)
            current_price = close_prices.iloc[-1]
            indicators['bb_position'] = (current_price - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1])
            indicators['bb_signal'] = self._bollinger_signal(indicators['bb_position'])
            
            # 4. 移动平均线
            sma_short = close_prices.rolling(window=self.sma_short).mean()
            sma_long = close_prices.rolling(window=self.sma_long).mean()
            indicators['sma_short'] = sma_short.iloc[-1] if not sma_short.empty else current_price
            indicators['sma_long'] = sma_long.iloc[-1] if not sma_long.empty else current_price
            indicators['sma_signal'] = self._sma_signal(indicators['sma_short'], indicators['sma_long'], current_price)
            
            # 5. 价格动量
            if len(close_prices) >= 5:
                price_change_5d = (current_price - close_prices.iloc[-5]) / close_prices.iloc[-5]
                indicators['momentum_5d'] = price_change_5d
                indicators['momentum_signal'] = self._momentum_signal(price_change_5d)
            else:
                indicators['momentum_5d'] = 0.0
                indicators['momentum_signal'] = 0.0
                
            # 6. 成交量趋势
            if len(volume) >= 10:
                volume_ma = volume.rolling(window=10).mean()
                current_volume = volume.iloc[-1]
                volume_ratio = current_volume / volume_ma.iloc[-1] if volume_ma.iloc[-1] > 0 else 1.0
                indicators['volume_ratio'] = volume_ratio
                indicators['volume_signal'] = self._volume_signal(volume_ratio)
            else:
                indicators['volume_ratio'] = 1.0
                indicators['volume_signal'] = 0.0
                
            return indicators
            
        except Exception as e:
            self.logger.error(f"计算技术指标失败: {e}")
            return {}
            
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """计算RSI指标"""
        try:
            delta = prices.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return rsi
        except Exception as e:
            self.logger.error(f"计算RSI失败: {e}")
            return pd.Series([50.0] * len(prices))
            
    def _calculate_macd(self, prices: pd.Series) -> tuple:
        """计算MACD指标"""
        try:
            ema_fast = prices.ewm(span=self.macd_fast).mean()
            ema_slow = prices.ewm(span=self.macd_slow).mean()
            macd_line = ema_fast - ema_slow
            macd_signal = macd_line.ewm(span=self.macd_signal).mean()
            macd_histogram = macd_line - macd_signal
            return macd_line, macd_signal, macd_histogram
        except Exception as e:
            self.logger.error(f"计算MACD失败: {e}")
            return pd.Series([0.0] * len(prices)), pd.Series([0.0] * len(prices)), pd.Series([0.0] * len(prices))
            
    def _calculate_bollinger_bands(self, prices: pd.Series) -> tuple:
        """计算布林带"""
        try:
            sma = prices.rolling(window=self.bb_period).mean()
            std = prices.rolling(window=self.bb_period).std()
            upper_band = sma + (std * self.bb_std)
            lower_band = sma - (std * self.bb_std)
            return upper_band, sma, lower_band
        except Exception as e:
            self.logger.error(f"计算布林带失败: {e}")
            return prices, prices, prices
            
    def _rsi_signal(self, rsi: float) -> float:
        """RSI信号"""
        if rsi <= self.rsi_oversold:
            return 1.0  # 买入信号
        elif rsi >= self.rsi_overbought:
            return -1.0  # 卖出信号
        else:
            return 0.0  # 中性
            
    def _macd_signal(self, macd: float, signal: float) -> float:
        """MACD信号"""
        if macd > signal and macd > 0:
            return 1.0  # 买入信号
        elif macd < signal and macd < 0:
            return -1.0  # 卖出信号
        else:
            return 0.0  # 中性
            
    def _bollinger_signal(self, bb_position: float) -> float:
        """布林带信号"""
        if bb_position <= 0.2:  # 接近下轨
            return 1.0  # 买入信号
        elif bb_position >= 0.8:  # 接近上轨
            return -1.0  # 卖出信号
        else:
            return 0.0  # 中性
            
    def _sma_signal(self, sma_short: float, sma_long: float, current_price: float) -> float:
        """移动平均线信号"""
        if sma_short > sma_long and current_price > sma_short:
            return 1.0  # 买入信号
        elif sma_short < sma_long and current_price < sma_short:
            return -1.0  # 卖出信号
        else:
            return 0.0  # 中性
            
    def _momentum_signal(self, momentum: float) -> float:
        """动量信号"""
        if momentum > 0.05:  # 5天涨幅超过5%
            return 1.0  # 买入信号
        elif momentum < -0.05:  # 5天跌幅超过5%
            return -1.0  # 卖出信号
        else:
            return 0.0  # 中性
            
    def _volume_signal(self, volume_ratio: float) -> float:
        """成交量信号"""
        if volume_ratio > 1.5:  # 成交量放大
            return 0.5  # 轻微买入信号
        elif volume_ratio < 0.5:  # 成交量萎缩
            return -0.5  # 轻微卖出信号
        else:
            return 0.0  # 中性
            
    def _generate_signal_score(self, indicators: Dict[str, float], data: Dict[str, Any]) -> tuple:
        """生成综合信号得分"""
        try:
            if not indicators:
                return 0.0, 0.0
                
            # 收集各个指标的信号
            signals = []
            weights = []
            
            # RSI信号 (权重: 0.25)
            if 'rsi_signal' in indicators:
                signals.append(indicators['rsi_signal'])
                weights.append(0.25)
                
            # MACD信号 (权重: 0.25)
            if 'macd_trend' in indicators:
                signals.append(indicators['macd_trend'])
                weights.append(0.25)
                
            # 布林带信号 (权重: 0.20)
            if 'bb_signal' in indicators:
                signals.append(indicators['bb_signal'])
                weights.append(0.20)
                
            # 移动平均线信号 (权重: 0.20)
            if 'sma_signal' in indicators:
                signals.append(indicators['sma_signal'])
                weights.append(0.20)
                
            # 动量信号 (权重: 0.10)
            if 'momentum_signal' in indicators:
                signals.append(indicators['momentum_signal'])
                weights.append(0.10)
                
            if not signals:
                return 0.0, 0.0
                
            # 归一化权重
            total_weight = sum(weights)
            weights = [w / total_weight for w in weights]
            
            # 计算加权平均信号
            weighted_signal = sum(s * w for s, w in zip(signals, weights))
            
            # 计算置信度（基于信号一致性）
            signal_consistency = len([s for s in signals if abs(s) > 0.1]) / len(signals)
            signal_strength = abs(weighted_signal)
            confidence = signal_consistency * signal_strength
            
            # 将信号转换为价格变化预期
            signal_score = weighted_signal * 0.1  # 将-1到1的信号转换为-10%到10%的预期变化
            
            return signal_score, confidence
            
        except Exception as e:
            self.logger.error(f"生成信号得分失败: {e}")
            return 0.0, 0.0
            
    def _calculate_quantity(self, confidence: float) -> int:
        """根据置信度计算建议数量"""
        base_quantity = 100
        quantity_multiplier = min(confidence * 2, 2.0)  # 最多2倍基础数量
        return int(base_quantity * quantity_multiplier)
        
    async def predict(self, symbol: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """预测接口，用于策略组合器"""
        signal = await self.generate_signal(symbol, data)
        if signal:
            # 将信号转换为预测格式
            if signal.signal_type == SignalType.BUY:
                prediction_value = signal.confidence
            elif signal.signal_type == SignalType.SELL:
                prediction_value = -signal.confidence
            else:
                prediction_value = 0.0
                
            return {
                'prediction': prediction_value,
                'confidence': signal.confidence,
                'signal': signal.signal_type.value,
                'price': signal.price,
                'extra_data': signal.extra_data
            }
        else:
            return {
                'prediction': 0.0,
                'confidence': 0.0,
                'signal': 'HOLD',
                'price': data.get('last_done', 0)
            } 