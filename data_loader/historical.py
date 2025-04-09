#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Union
import os
import logging

from longport.openapi import (
    Config, 
    QuoteContext, 
    Period, 
    AdjustType,
    Candlestick,
    SecurityQuote,
    Market
)

from utils import ConfigLoader, setup_logger, setup_longport_env

class HistoricalDataLoader:
    """历史行情数据加载器，负责获取和处理长桥API的历史K线数据"""
    
    def __init__(self, config_loader: ConfigLoader):
        """
        初始化历史数据加载器
        
        Args:
            config_loader: 配置加载器
        """
        self.config = config_loader
        self.logger = setup_logger(
            "historical_data", 
            self.config.get("logging.level", "INFO"),
            self.config.get("logging.file")
        )
        
        # 确保环境变量已设置
        setup_longport_env()
        
        # 使用环境变量创建配置
        self.longport_config = Config.from_env()
        
        # 初始化行情上下文
        self.quote_ctx: Optional[QuoteContext] = None
        
        # 缓存目录
        self.cache_dir = os.path.join(os.getcwd(), "data_cache")
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
    
    async def initialize(self):
        """初始化行情上下文"""
        if self.quote_ctx is None:
            self.logger.info("正在初始化行情上下文...")
            # 设置网络超时时间
            os.environ["LONGBRIDGE_NETWORK_TIMEOUT"] = "30"  # 设置网络请求超时为30秒
            try:
                self.quote_ctx = QuoteContext(self.longport_config)
                self.logger.info("行情上下文初始化完成")
            except Exception as e:
                self.logger.error(f"行情上下文初始化失败: {e}")
                raise
    
    async def get_candlesticks(
        self, 
        symbol: str, 
        period: Union[str, Period] = Period.Day, 
        count: int = None,  # 修改为可选参数
        adjust_type: AdjustType = AdjustType.NoAdjust,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        获取K线数据
        
        Args:
            symbol: 股票代码
            period: K线周期，可以是字符串或Period枚举
            count: 获取的K线数量，如果为None则使用配置中的historical_days
            adjust_type: 复权类型
            use_cache: 是否使用缓存
            
        Returns:
            K线数据的DataFrame
        """
        await self.initialize()
        
        # 转换period为枚举类型
        if isinstance(period, str):
            period = getattr(Period, period)
            
        # 获取period名称，用于日志和缓存文件名
        period_name = str(period).replace("Period.", "")
        
        # 如果count为None，使用配置中的historical_days
        if count is None:
            count = self.config.get("quote.historical_days", 1250)  # 默认5年数据
            self.logger.info(f"使用配置的历史数据天数: {count}")
            
        # 检查缓存
        cache_file = self._get_cache_filename(symbol, period, adjust_type)
        if use_cache and os.path.exists(cache_file):
            cache_df = pd.read_csv(cache_file, parse_dates=["timestamp"])
            cache_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(cache_file))
            
            # 如果缓存是当天的，且数据量足够，直接使用缓存
            if cache_age < timedelta(days=1) and len(cache_df) >= count:
                self.logger.info(f"使用缓存数据: {symbol}, {period_name}")
                return cache_df.tail(count).reset_index(drop=True)
        
        self.logger.info(f"从API获取K线数据: {symbol}, {period_name}, count={count}")
        
        try:
            # 将大量数据请求分成多个小批次
            batch_size = 500  # 每批次请求500条数据
            all_candlesticks = []
            
            # 计算需要多少个批次
            num_batches = (count + batch_size - 1) // batch_size
            
            for batch in range(num_batches):
                # 计算当前批次的起始位置和数量
                start_idx = batch * batch_size
                current_batch_size = min(batch_size, count - start_idx)
                
                self.logger.info(f"获取第 {batch + 1}/{num_batches} 批次数据，数量: {current_batch_size}")
                
                # 从API获取K线数据
                candlesticks = self.quote_ctx.candlesticks(
                    symbol, 
                    period, 
                    current_batch_size, 
                    adjust_type
                )
                
                if not candlesticks:
                    self.logger.warning(f"第 {batch + 1} 批次没有数据")
                    break
                    
                all_candlesticks.extend(candlesticks)
                
                # 如果不是最后一个批次，等待一小段时间避免请求过于频繁
                if batch < num_batches - 1:
                    await asyncio.sleep(1)
            
            if not all_candlesticks:
                self.logger.error(f"没有获取到任何K线数据: {symbol}")
                return pd.DataFrame()
            
            # 转换为DataFrame
            df = self._convert_candlesticks_to_df(all_candlesticks)
            
            # 保存到缓存
            if use_cache and not df.empty:
                df.to_csv(cache_file, index=False)
                self.logger.debug(f"已保存K线数据到缓存: {cache_file}")
                
            return df
        except Exception as e:
            self.logger.error(f"获取K线数据失败: {symbol}, {e}")
            return pd.DataFrame()
    
    async def get_multiple_candlesticks(
        self, 
        symbols: List[str], 
        period: Union[str, Period] = Period.Day, 
        count: int = 100, 
        adjust_type: AdjustType = AdjustType.NoAdjust,
        use_cache: bool = True
    ) -> Dict[str, pd.DataFrame]:
        """
        批量获取多个股票的K线数据
        
        Args:
            symbols: 股票代码列表
            period: K线周期
            count: 获取的K线数量
            adjust_type: 复权类型
            use_cache: 是否使用缓存
            
        Returns:
            字典，键为股票代码，值为对应的K线DataFrame
        """
        self.logger.info(f"批量获取K线数据: {symbols}")
        result = {}
        tasks = []
        
        # 创建所有异步任务
        for symbol in symbols:
            task = asyncio.create_task(
                self.get_candlesticks(symbol, period, count, adjust_type, use_cache)
            )
            tasks.append((symbol, task))
        
        # 等待所有任务完成
        for symbol, task in tasks:
            try:
                result[symbol] = await task
                self.logger.debug(f"成功获取 {symbol} 的K线数据，共 {len(result[symbol])} 条")
            except Exception as e:
                self.logger.error(f"获取 {symbol} 的K线数据失败: {e}")
                result[symbol] = pd.DataFrame()
                
        return result
    
    async def get_historical_trade_days(self, market: Union[str, Market], start: str, end: str):
        """
        获取交易日历
        
        Args:
            market: 市场，可以是字符串或Market枚举
            start: 开始日期，格式为YYYY-MM-DD
            end: 结束日期，格式为YYYY-MM-DD
            
        Returns:
            交易日列表
        """
        await self.initialize()
        
        # 转换market为枚举类型
        if isinstance(market, str):
            market = getattr(Market, market)
            
        self.logger.info(f"获取交易日历: {market.name}, {start} ~ {end}")
        
        try:
            # 从API获取交易日历
            # 去掉await关键字
            trade_days = self.quote_ctx.trading_days(market, start, end)
            
            return trade_days
        except Exception as e:
            self.logger.error(f"获取交易日历失败: {e}")
            return []
    
    async def get_historical_quote(self, symbol: str, date: str):
        """
        获取历史日线行情
        
        Args:
            symbol: 股票代码
            date: 日期，格式为YYYY-MM-DD
            
        Returns:
            历史行情数据
        """
        await self.initialize()
        
        self.logger.info(f"获取历史日线行情: {symbol}, {date}")
        
        try:
            # 从API获取历史日线行情
            # 去掉await关键字
            quote = self.quote_ctx.historical_quote(symbol, date)
            
            return quote
        except Exception as e:
            self.logger.error(f"获取历史日线行情失败: {symbol}, {date}, {e}")
            return None
    
    def prepare_feature_data(self, df: pd.DataFrame, lookback_period: int = 30, target_col: str = "close"):
        """
        准备特征数据，用于模型训练
        
        Args:
            df: K线数据的DataFrame
            lookback_period: 回看周期
            target_col: 目标列名
            
        Returns:
            X: 特征数据，shape=(样本数, 回看周期, 特征数)
            y: 标签数据，shape=(样本数,)
        """
        # 确保数据按时间排序
        df = df.sort_values("timestamp").reset_index(drop=True)
        
        # 提取特征列
        feature_cols = self.config.get("strategy.training.features", 
                                       ["close", "volume", "high", "low"])
        
        # 检查所有特征列是否存在
        for col in feature_cols:
            if col not in df.columns:
                raise ValueError(f"特征列 {col} 不存在于数据中")
                
        # 提取特征数据
        data = df[feature_cols].values
        
        # 标准化/归一化
        data_norm = np.zeros_like(data, dtype=np.float32)
        for i in range(data.shape[1]):
            # 使用滑动窗口进行归一化，避免未来数据泄露
            for j in range(lookback_period, len(data)):
                window = data[j-lookback_period:j, i]
                min_val = window.min()
                max_val = window.max()
                if max_val > min_val:
                    data_norm[j, i] = (data[j, i] - min_val) / (max_val - min_val)
                else:
                    data_norm[j, i] = 0.5
        
        # 创建时间序列数据
        X, y = [], []
        target_idx = feature_cols.index(target_col)
        
        for i in range(lookback_period, len(data) - 1):
            X.append(data_norm[i-lookback_period:i])
            # 预测下一个收盘价相对于当前收盘价的变化
            current = data[i, target_idx]
            next_val = data[i+1, target_idx]
            # 计算价格变化百分比作为预测目标
            y.append((next_val - current) / current if current != 0 else 0)
            
        return np.array(X), np.array(y)
    
    def _convert_candlesticks_to_df(self, candlesticks: List[Candlestick]) -> pd.DataFrame:
        """将K线列表转换为DataFrame"""
        if not candlesticks:
            return pd.DataFrame()
            
        data = []
        
        for candle in candlesticks:
            data.append({
                "timestamp": candle.timestamp,
                "open": float(candle.open),
                "high": float(candle.high),
                "low": float(candle.low),
                "close": float(candle.close),
                "volume": int(candle.volume),
                "turnover": float(candle.turnover)
            })
            
        return pd.DataFrame(data)
    
    def _get_cache_filename(self, symbol: str, period: Period, adjust_type: AdjustType) -> str:
        """
        获取缓存文件名
        
        Args:
            symbol: 股票代码
            period: K线周期
            adjust_type: 复权类型
            
        Returns:
            缓存文件路径
        """
        # 获取period和adjust_type的字符串表示
        period_str = str(period).replace("Period.", "")
        adjust_str = str(adjust_type).replace("AdjustType.", "")
        
        # 构建缓存文件名
        filename = f"{symbol}_{period_str}_{adjust_str}.csv"
        return os.path.join(self.cache_dir, filename)
    
    async def close(self):
        """关闭行情上下文"""
        if self.quote_ctx:
            self.logger.info("关闭行情上下文")
            self.quote_ctx.close()
            self.quote_ctx = None
