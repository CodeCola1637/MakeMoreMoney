#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import logging
from typing import List, Dict, Any, Set, Callable, Optional, Union
import time
import pandas as pd
from datetime import datetime
import os

from longport.openapi import (
    Config, 
    QuoteContext, 
    SubType, 
    PushQuote, 
    PushDepth, 
    PushBrokers, 
    PushTrades,
    Trade,
    Brokers,
    SecurityQuote,
    Depth
)

from utils import ConfigLoader, setup_logger, setup_longport_env

# 定义回调类型
PushEventType = Union[PushQuote, PushDepth, PushBrokers, PushTrades]
EventCallback = Callable[[str, PushEventType], None]

class RealtimeDataManager:
    """实时行情数据管理器，负责订阅和处理长桥API的实时行情数据"""
    
    def __init__(
        self, 
        config_loader: ConfigLoader
    ):
        """
        初始化实时数据管理器
        
        Args:
            config_loader: 配置加载器
        """
        self.config = config_loader
        
        self.logger = setup_logger(
            "realtime_data", 
            self.config.get("logging.level", "INFO"),
            self.config.get("logging.file")
        )
        
        # 初始化长桥API配置
        # 使用环境变量创建配置，而不是使用配置文件中的值
        setup_longport_env()  # 确保环境变量已设置
        self.longport_config = Config.from_env()
        
        # 初始化行情上下文
        self.quote_ctx: Optional[QuoteContext] = None
        
        # 保存最新行情数据
        self.latest_quotes: Dict[str, SecurityQuote] = {}
        self.latest_depths: Dict[str, Depth] = {}
        self.latest_trades: Dict[str, List[Trade]] = {}
        self.latest_brokers: Dict[str, Brokers] = {}
        
        # 订阅的股票代码
        self.subscribed_symbols: Set[str] = set()
        
        # 用户自定义回调函数
        self.user_callbacks: Dict[str, List[EventCallback]] = {
            "Quote": [],
            "Depth": [],
            "Brokers": [],
            "Trade": []
        }
        
    async def initialize(self):
        """初始化行情上下文"""
        # 如果已经初始化，则直接返回
        if self.quote_ctx:
            return
            
        self.logger.info("正在初始化行情上下文...")
            
        # 设置网络超时时间
        os.environ["LONGBRIDGE_NETWORK_TIMEOUT"] = "120"  # 增加超时时间到120秒
        
        # 重试逻辑
        max_retries = 3
        retry_delay = 5  # 秒
        
        for attempt in range(1, max_retries + 1):
            try:
                self.logger.info(f"尝试创建行情上下文 (尝试 {attempt}/{max_retries})...")
                
                # 创建行情上下文，新版本SDK不再支持设置推送回调
                self.quote_ctx = QuoteContext(self.longport_config)
                
                # 验证连接
                # 尝试获取市场状态来验证连接是否成功
                market_status = self.quote_ctx.trading_session()
                self.logger.info(f"成功获取市场状态，API连接正常")
                
                self.logger.info("行情上下文初始化完成")
                return
            except Exception as e:
                self.logger.error(f"行情上下文初始化失败 (尝试 {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    self.logger.info(f"等待 {retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                else:
                    self.logger.error("已达到最大重试次数，无法初始化行情上下文")
                    raise
        
    async def start(self):
        """启动行情管理器"""
        await self.initialize()
        self.logger.info("行情数据管理器已启动")
        
    async def stop(self):
        """停止行情管理器"""
        await self.close()
            
    async def subscribe_stock(self, symbol: str):
        """订阅单个股票行情"""
        return await self.subscribe([symbol], ["Quote", "Depth", "Brokers", "Trade"])
        
    def register_quote_callback(self, symbol: str, callback: Callable):
        """注册股票行情更新回调"""
        if symbol not in self.subscribed_symbols:
            self.logger.warning(f"股票{symbol}未订阅，回调可能不会被触发")
            
        def wrapped_callback(sym: str, quote: PushQuote):
            if sym == symbol:
                callback(quote.quote)
                
        self.register_callback("Quote", wrapped_callback)
        
    def register_callback(self, event_type: str, callback: EventCallback):
        """
        注册回调函数
        
        Args:
            event_type: 事件类型，如"Quote", "Depth", "Brokers", "Trade"
            callback: 回调函数
        """
        if event_type in self.user_callbacks:
            self.user_callbacks[event_type].append(callback)
            self.logger.debug(f"已注册{event_type}回调函数")
        else:
            self.logger.warning(f"未知事件类型: {event_type}")
            
    async def subscribe(self, symbols: List[str], sub_types: List[Union[str, SubType]], is_first_push: bool = True):
        """
        订阅行情
        
        Args:
            symbols: 股票代码列表
            sub_types: 订阅类型列表，可以是字符串或SubType枚举
            is_first_push: 是否立即推送最新数据
            
        Returns:
            订阅结果
        """
        if not self.quote_ctx:
            await self.initialize()
            
        # 转换订阅类型为SubType枚举
        enum_sub_types = []
        for sub_type in sub_types:
            if isinstance(sub_type, str):
                enum_sub_types.append(getattr(SubType, sub_type))
            else:
                enum_sub_types.append(sub_type)
                
        self.logger.info(f"订阅行情: {symbols}, 类型: {[t.name if hasattr(t, 'name') else t for t in sub_types]}")
        
        # 订阅行情
        try:
            # API 可能需要单独处理每个股票
            for symbol in symbols:
                try:
                    self.quote_ctx.subscribe([symbol], enum_sub_types, is_first_push)
                    self.subscribed_symbols.add(symbol)
                    self.logger.debug(f"成功订阅 {symbol}")
                except Exception as e:
                    self.logger.error(f"订阅 {symbol} 失败: {e}")
            
            # 尝试获取并保存最新行情
            for symbol in symbols:
                try:
                    if SubType.Quote in enum_sub_types or "Quote" in sub_types:
                        quote = self.quote_ctx.quote(symbol)
                        self.latest_quotes[symbol] = quote
                        self.logger.debug(f"获取{symbol}的实时行情: {quote.last_done}")
                except Exception as e:
                    self.logger.warning(f"获取{symbol}最新行情失败: {e}")
            
            return True
        except Exception as e:
            self.logger.error(f"订阅行情失败: {e}")
            return False
    
    async def unsubscribe(self, symbols: List[str], sub_types: List[Union[str, SubType]]):
        """
        取消订阅行情
        
        Args:
            symbols: 股票代码列表
            sub_types: 订阅类型列表
            
        Returns:
            取消订阅结果
        """
        if not self.quote_ctx:
            self.logger.warning("行情上下文未初始化，无法取消订阅")
            return False
            
        # 转换订阅类型为SubType枚举
        enum_sub_types = []
        for sub_type in sub_types:
            if isinstance(sub_type, str):
                enum_sub_types.append(getattr(SubType, sub_type))
            else:
                enum_sub_types.append(sub_type)
                
        self.logger.info(f"取消订阅行情: {symbols}, 类型: {[t.name if hasattr(t, 'name') else t for t in sub_types]}")
        
        # 取消订阅行情
        try:
            # API 可能需要单独处理每个股票
            for symbol in symbols:
                try:
                    self.quote_ctx.unsubscribe([symbol], enum_sub_types)
                    if symbol in self.subscribed_symbols:
                        self.subscribed_symbols.remove(symbol)
                    self.logger.debug(f"成功取消订阅 {symbol}")
                except Exception as e:
                    self.logger.error(f"取消订阅 {symbol} 失败: {e}")
                    
            return True
        except Exception as e:
            self.logger.error(f"取消订阅行情失败: {e}")
            return False
    
    async def get_quote(self, symbols: List[str]):
        """
        获取股票实时行情
        
        Args:
            symbols: 股票代码列表
            
        Returns:
            实时行情数据
        """
        if not self.quote_ctx:
            await self.initialize()
            
        result = {}
        
        try:
            quotes = self.quote_ctx.quote(symbols)
            
            # SDK 调用返回单个对象或列表，需要统一处理
            if not isinstance(quotes, list):
                quotes = [quotes]
                
            for i, quote in enumerate(quotes):
                symbol = symbols[i] if i < len(symbols) else quote.symbol
                result[symbol] = quote
                
                # 更新最新行情
                self.latest_quotes[symbol] = quote
                self.logger.debug(f"获取{symbol}实时行情: {quote.last_done}")
                
        except Exception as e:
            self.logger.error(f"获取实时行情失败: {e}")
            
            # 尝试单个获取
            for symbol in symbols:
                try:
                    quote = self.quote_ctx.quote(symbol)
                    result[symbol] = quote
                    self.latest_quotes[symbol] = quote
                    self.logger.debug(f"单独获取{symbol}实时行情: {quote.last_done}")
                except Exception as e:
                    self.logger.error(f"获取{symbol}实时行情失败: {e}")
                
        return result
    
    async def get_depth(self, symbol: str):
        """
        获取股票深度行情
        
        Args:
            symbol: 股票代码
            
        Returns:
            深度行情数据
        """
        if not self.quote_ctx:
            await self.initialize()
            
        try:
            depth = self.quote_ctx.depth(symbol)
            
            # 更新最新深度行情
            self.latest_depths[symbol] = depth
            
            return depth
        except Exception as e:
            self.logger.error(f"获取{symbol}深度行情失败: {e}")
            return None
    
    async def get_trades(self, symbol: str, count: int = 10):
        """
        获取股票最近成交记录
        
        Args:
            symbol: 股票代码
            count: 获取条数
            
        Returns:
            成交记录数据
        """
        if not self.quote_ctx:
            await self.initialize()
            
        try:
            trades = self.quote_ctx.trades(symbol, count)
            
            # 更新最新成交记录
            self.latest_trades[symbol] = trades
            
            return trades
        except Exception as e:
            self.logger.error(f"获取{symbol}成交记录失败: {e}")
            return None
    
    async def get_brokers(self, symbol: str):
        """
        获取股票经纪队列
        
        Args:
            symbol: 股票代码
            
        Returns:
            经纪队列数据
        """
        if not self.quote_ctx:
            await self.initialize()
            
        try:
            brokers = self.quote_ctx.brokers(symbol)
            
            # 更新最新经纪队列
            self.latest_brokers[symbol] = brokers
            
            return brokers
        except Exception as e:
            self.logger.error(f"获取{symbol}经纪队列失败: {e}")
            return None
    
    def get_latest_quote(self, symbol: str) -> Optional[SecurityQuote]:
        """
        获取最新行情数据
        
        Args:
            symbol: 股票代码
            
        Returns:
            最新行情数据，如果不存在则返回None
        """
        return self.latest_quotes.get(symbol)
    
    async def get_candlesticks(self, symbol: str, period: str = "1d", count: int = 100):
        """
        获取K线数据
        
        Args:
            symbol: 股票代码
            period: K线周期，如"1m", "5m", "15m", "30m", "60m", "1d", "1w", "1M"
            count: 获取条数
            
        Returns:
            K线数据，DataFrame格式
        """
        if not self.quote_ctx:
            await self.initialize()
            
        try:
            # 转换周期格式
            period_map = {
                "1m": "OneMInute",
                "5m": "FiveMinute",
                "15m": "FifteenMinute",
                "30m": "ThirtyMinute",
                "60m": "SixtyMinute",
                "1d": "Day",
                "1w": "Week",
                "1M": "Month"
            }
            
            if period in period_map:
                from longport.openapi import Period
                enum_period = getattr(Period, period_map[period])
            else:
                self.logger.error(f"不支持的K线周期: {period}")
                return pd.DataFrame()
                
            # 获取K线数据
            bars = self.quote_ctx.candlesticks(symbol, enum_period, count)
            
            # 转换为DataFrame
            data = []
            for bar in bars:
                data.append({
                    'time': bar.timestamp,
                    'open': bar.open,
                    'high': bar.high,
                    'low': bar.low,
                    'close': bar.close,
                    'volume': bar.volume,
                    'turnover': bar.turnover
                })
                
            df = pd.DataFrame(data)
            if not df.empty and 'time' in df.columns:
                df.set_index('time', inplace=True)
                
            return df
        except Exception as e:
            self.logger.error(f"获取{symbol}的K线数据失败: {e}")
            return pd.DataFrame()
    
    async def close(self):
        """关闭行情上下文"""
        if self.quote_ctx:
            try:
                # 取消所有订阅
                if self.subscribed_symbols:
                    symbols = list(self.subscribed_symbols)
                    self.logger.info(f"取消所有订阅: {symbols}")
                    try:
                        self.quote_ctx.unsubscribe(symbols)
                    except Exception as e:
                        self.logger.warning(f"取消订阅失败: {e}")
                
                # 关闭上下文
                self.logger.info("关闭行情上下文")
                self.quote_ctx = None
            except Exception as e:
                self.logger.error(f"关闭行情上下文失败: {e}")
                raise
        else:
            self.logger.debug("行情上下文未初始化，无需关闭")
