#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import logging
from typing import List, Dict, Any, Set, Callable, Optional, Union
import time
import pandas as pd
from datetime import datetime, timedelta
import os
import traceback
from collections import defaultdict

from longport.openapi import (
    Config, 
    QuoteContext, 
    SubType, 
    Trade,
    Brokers,
    SecurityQuote,
    Depth
)

from utils import ConfigLoader, setup_logger, setup_longport_env

# 定义回调类型
EventCallback = Callable[[str, Any], None]

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
        
        # 数据缓存机制
        self.quote_cache: Dict[str, Dict[str, Any]] = {}  # 缓存最近100条行情数据
        self.cache_size = 100  # 缓存大小
        self.last_update_time: Dict[str, datetime] = {}  # 记录每个股票的最后更新时间
        
        # 数据质量监控
        self.data_quality: Dict[str, Dict[str, Any]] = {}  # 记录数据质量指标
        self.data_quality_threshold = self.config.get('data_quality_threshold', 0.1)
        self.last_quality_check = {}
        self.quality_check_interval = 300  # 每5分钟检查一次数据质量
        
        # 订阅的股票代码
        self.subscribed_symbols: Set[str] = set()
        
        # 用户自定义回调函数
        self.user_callbacks: Dict[str, List[EventCallback]] = {
            "Quote": [],
            "Depth": [],
            "Brokers": [],
            "Trade": []
        }
        
        # 获取当前事件循环
        self._loop = asyncio.get_event_loop()
        
        # 初始化任务列表
        self._tasks = []
        
    async def initialize(self):
        """初始化行情上下文"""
        try:
            self.logger.info("正在初始化行情上下文...")
            
            # 设置网络超时时间
            os.environ["LONGBRIDGE_NETWORK_TIMEOUT"] = "120"  # 增加超时时间到120秒
            
            # 重试逻辑
            max_retries = 3
            retry_delay = 5  # 秒
            
            for attempt in range(1, max_retries + 1):
                try:
                    self.logger.info(f"尝试创建行情上下文 (尝试 {attempt}/{max_retries})...")
                    
                    # 创建行情上下文
                    self.quote_ctx = QuoteContext(self.longport_config)
                    
                    # 验证连接
                    # 尝试获取市场状态来验证连接是否成功
                    market_status = self.quote_ctx.trading_session()
                    self.logger.info(f"成功获取市场状态，API连接正常")
                    
                    # 设置推送回调函数
                    self._setup_push_callbacks()
                    
                    self.logger.info("行情上下文初始化完成")
                    return True
                except Exception as e:
                    self.logger.error(f"行情上下文初始化失败 (尝试 {attempt}/{max_retries}): {e}")
                    if attempt < max_retries:
                        self.logger.info(f"等待 {retry_delay} 秒后重试...")
                        await asyncio.sleep(retry_delay)
                        if self.quote_ctx:
                            self.quote_ctx = None
                    else:
                        self.logger.error("已达到最大重试次数，无法初始化行情上下文")
                        raise
                        
        except Exception as e:
            self.logger.error(f"初始化行情上下文失败: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            raise
            
    def _setup_push_callbacks(self):
        """设置推送回调函数"""
        try:
            # 设置推送回调
            if hasattr(self.quote_ctx, 'set_on_quote'):
                def quote_callback(symbol: str, quote: Any):
                    self._loop.call_soon_threadsafe(
                        asyncio.create_task,
                        self._on_quote_push(symbol, quote)
                    )
                self.quote_ctx.set_on_quote(quote_callback)
                
            if hasattr(self.quote_ctx, 'set_on_depth'):
                def depth_callback(symbol: str, depth: Any):
                    self._loop.call_soon_threadsafe(
                        asyncio.create_task,
                        self._on_depth_push(symbol, depth)
                    )
                self.quote_ctx.set_on_depth(depth_callback)
                
            if hasattr(self.quote_ctx, 'set_on_brokers'):
                def brokers_callback(symbol: str, brokers: Any):
                    self._loop.call_soon_threadsafe(
                        asyncio.create_task,
                        self._on_brokers_push(symbol, brokers)
                    )
                self.quote_ctx.set_on_brokers(brokers_callback)
                
            if hasattr(self.quote_ctx, 'set_on_trade'):
                def trade_callback(symbol: str, trade: Any):
                    self._loop.call_soon_threadsafe(
                        asyncio.create_task,
                        self._on_trade_push(symbol, trade)
                    )
                self.quote_ctx.set_on_trade(trade_callback)
                
            self.logger.info("已设置实时行情推送回调")
        except Exception as e:
            self.logger.error(f"设置推送回调函数失败: {e}")
    
    def _check_data_quality(self, symbol: str) -> bool:
        """检查数据质量"""
        current_time = time.time()
        if symbol in self.last_quality_check:
            if current_time - self.last_quality_check[symbol] < self.quality_check_interval:
                return True
        
        self.last_quality_check[symbol] = current_time
        
        if symbol not in self.latest_quotes:
            self.logger.warning(f"{symbol} 没有最新行情数据")
            return False
            
        quote = self.latest_quotes[symbol]
        if not quote:
            self.logger.warning(f"{symbol} 行情数据为空")
            return False
            
        # 初始化质量分数
        quality_score = 0
        total_checks = 0
        
        # 1. 检查关键字段是否存在
        required_fields = ['last_done', 'open', 'volume', 'turnover']
        for field in required_fields:
            if hasattr(quote, field) and getattr(quote, field) is not None:
                quality_score += 1
            else:
                self.logger.warning(f"{symbol} 缺少字段: {field}")
            total_checks += 1
            
        # 2. 检查价格合理性
        if quote.last_done > 0:
            quality_score += 1
            # 检查价格变动范围
            if hasattr(quote, 'prev_close') and quote.prev_close > 0:
                price_change = abs(quote.last_done - quote.prev_close) / quote.prev_close
                if price_change > 0.1:  # 价格变动超过10%需要特别关注
                    self.logger.warning(f"{symbol} 价格变动异常: {price_change:.2%}")
        else:
            self.logger.warning(f"{symbol} 价格数据异常: last_done={quote.last_done}")
        total_checks += 1
        
        # 3. 检查成交量合理性
        if quote.volume > 0:
            quality_score += 1
        else:
            self.logger.warning(f"{symbol} 成交量为0")
        total_checks += 1
        
        # 4. 检查成交额合理性
        if quote.turnover > 0:
            quality_score += 1
        else:
            self.logger.warning(f"{symbol} 成交额为0")
        total_checks += 1
        
        # 5. 检查高低价合理性
        if hasattr(quote, 'high') and hasattr(quote, 'low'):
            if quote.high > 0 and quote.low > 0:
                if quote.high >= quote.low and quote.high >= quote.last_done >= quote.low:
                    quality_score += 1
                else:
                    self.logger.warning(f"{symbol} 价格区间异常: high={quote.high}, low={quote.low}, last={quote.last_done}")
            else:
                # 如果 high 或 low 为 0，使用 last_done 作为参考
                if quote.last_done > 0:
                    quality_score += 1
                else:
                    self.logger.warning(f"{symbol} 价格数据异常: high={quote.high}, low={quote.low}, last={quote.last_done}")
        else:
            # 如果没有 high 和 low 字段，使用 last_done 作为参考
            if quote.last_done > 0:
                quality_score += 1
            else:
                self.logger.warning(f"{symbol} 缺少价格区间数据")
        total_checks += 1
        
        # 计算最终质量分数
        final_score = quality_score / total_checks
        
        # 记录详细的质量信息
        quality_info = {
            "score": final_score,
            "checks": total_checks,
            "passed": quality_score,
            "timestamp": current_time
        }
        
        if final_score < self.data_quality_threshold:
            self.logger.warning(f"{symbol} 数据质量较低: {final_score:.2f}, 通过检查: {quality_score}/{total_checks}")
            self.logger.info(f"{symbol} 详细质量信息: {quality_info}")
            return False
            
        return True
        
    async def _on_quote_push(self, symbol: str, quote: Any):
        """处理实时行情推送"""
        try:
            # 更新最新行情
            self.latest_quotes[symbol] = quote
            
            # 更新数据缓存
            if symbol not in self.quote_cache:
                self.quote_cache[symbol] = []
            self.quote_cache[symbol].append(quote)
            
            # 限制缓存大小
            if len(self.quote_cache[symbol]) > self.cache_size:
                self.quote_cache[symbol].pop(0)
                
            # 更新最后更新时间
            self.last_update_time[symbol] = datetime.now()
            
            # 记录日志
            self.logger.debug(f"收到行情推送: {symbol}, 价格: {quote.last_done}")
            
            # 调用用户回调函数
            for callback in self.user_callbacks.get("Quote", []):
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(symbol, quote)
                    else:
                        callback(symbol, quote)
                except Exception as e:
                    self.logger.error(f"执行Quote回调函数失败: {str(e)}")
                    
        except Exception as e:
            self.logger.error(f"处理实时行情推送失败: {str(e)}")
            
    async def _on_depth_push(self, symbol: str, depth: Any) -> None:
        """处理深度行情推送"""
        try:
            self.logger.debug(f"收到{symbol}的深度行情推送")
            self.latest_depths[symbol] = depth
            await self._notify_observers(symbol, "Depth", depth)
        except Exception as e:
            self.logger.error(f"处理{symbol}的深度行情推送时出错: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            
    async def _on_brokers_push(self, symbol: str, brokers: Any) -> None:
        """处理经纪商队列推送"""
        try:
            self.logger.debug(f"收到{symbol}的经纪商队列推送")
            self.latest_brokers[symbol] = brokers
            await self._notify_observers(symbol, "Brokers", brokers)
        except Exception as e:
            self.logger.error(f"处理{symbol}的经纪商队列推送时出错: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            
    async def _on_trade_push(self, symbol: str, trade: Any) -> None:
        """处理成交推送"""
        try:
            self.logger.debug(f"收到{symbol}的成交推送")
            if symbol not in self.latest_trades:
                self.latest_trades[symbol] = []
            self.latest_trades[symbol].append(trade)
            # 只保留最近的100条成交记录
            if len(self.latest_trades[symbol]) > 100:
                self.latest_trades[symbol] = self.latest_trades[symbol][-100:]
            await self._notify_observers(symbol, "Trade", trade)
        except Exception as e:
            self.logger.error(f"处理{symbol}的成交推送时出错: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
        
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
            
        def wrapped_callback(sym: str, quote):
            if sym == symbol:
                callback(quote)
                
        self.register_callback("Quote", wrapped_callback)
        
    def register_callback(self, event_type: str, callback: EventCallback):
        """注册回调函数
        
        Args:
            event_type: 事件类型，如"Quote", "Depth", "Brokers", "Trade"
            callback: 回调函数，接收symbol和数据
        """
        if event_type in self.user_callbacks:
            self.user_callbacks[event_type].append(callback)
            self.logger.info(f"已注册{event_type}类型的回调函数")
        else:
            self.logger.error(f"未知的事件类型: {event_type}")
            
    async def subscribe(self, symbols: List[str], sub_types: List[Union[str, SubType]] = None) -> bool:
        """
        订阅行情
        
        Args:
            symbols: 股票代码列表
            sub_types: 订阅类型列表，默认为None，表示使用配置中的订阅类型
            
        Returns:
            bool: 是否成功
        """
        try:
            self.logger.info(f"订阅行情: {symbols}, 类型: {sub_types}")
            
            # 确保行情上下文已初始化
            if not self.quote_ctx:
                success = await self.initialize()
                if not success:
                    self.logger.error("初始化行情上下文失败")
                    return False
                
            # 如果没有指定订阅类型，使用配置中的类型
            if sub_types is None:
                # 从配置中获取订阅类型
                sub_types = self.config.get("quote", {}).get("sub_types", ["Quote"])
                self.logger.info(f"使用配置中的订阅类型: {sub_types}")
                
            # 确保sub_types是SubType类型
            converted_types = []
            for sub_type in sub_types:
                if isinstance(sub_type, str):
                    try:
                        # 直接使用SubType的枚举值
                        if sub_type == "Quote":
                            converted_types.append(SubType.Quote)
                        elif sub_type == "Depth":
                            converted_types.append(SubType.Depth)
                        elif sub_type == "Brokers":
                            converted_types.append(SubType.Brokers)
                        elif sub_type == "Trade":
                            converted_types.append(SubType.Trade)
                        else:
                            self.logger.warning(f"不支持的订阅类型: {sub_type}")
                    except Exception as e:
                        self.logger.warning(f"转换订阅类型失败: {sub_type}, 错误: {e}")
                else:
                    converted_types.append(sub_type)
                    
            # 如果没有有效的订阅类型，使用默认的Quote类型
            if not converted_types:
                self.logger.warning("没有有效的订阅类型，使用默认的Quote类型")
                converted_types = [SubType.Quote]
                
            # 订阅行情
            try:
                if not self.quote_ctx:
                    self.logger.error("行情上下文未初始化")
                    return False
                    
                # 确保订阅类型不为空
                if not converted_types:
                    self.logger.error("订阅类型为空")
                    return False
                    
                # 订阅行情
                self.quote_ctx.subscribe(symbols, converted_types)
                
                # 更新已订阅的股票列表
                self.subscribed_symbols.update(symbols)
                
                self.logger.info(f"成功订阅行情: {symbols}, 类型: {converted_types}")
                return True
            except Exception as e:
                self.logger.error(f"订阅行情失败: {str(e)}")
                # 尝试重新初始化
                success = await self.initialize()
                if not success:
                    self.logger.error("重新初始化行情上下文失败")
                    return False
                    
                try:
                    self.quote_ctx.subscribe(symbols, converted_types)
                    # 更新已订阅的股票列表
                    self.subscribed_symbols.update(symbols)
                    self.logger.info(f"重新初始化后成功订阅行情: {symbols}, 类型: {converted_types}")
                    return True
                except Exception as e:
                    self.logger.error(f"重新初始化后订阅行情仍然失败: {str(e)}")
                    return False
                
        except Exception as e:
            self.logger.error(f"订阅行情失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return False
            
    async def _on_quote(self, symbol: str, quote: Any) -> None:
        """
        处理实时报价数据
        
        Args:
            symbol: 股票代码
            quote: 报价数据
        """
        try:
            if not quote:
                self.logger.warning(f"收到{symbol}的空报价数据")
                return
                
            # 转换数据格式
            quote_data = {
                'symbol': symbol,
                'timestamp': datetime.now(),
                'last_price': float(quote.last_price),
                'open': float(quote.open),
                'high': float(quote.high),
                'low': float(quote.low),
                'close': float(quote.close),
                'volume': int(quote.volume),
                'turnover': float(quote.turnover),
                'bid_price': [float(p) for p in quote.bid_price],
                'bid_volume': [int(v) for v in quote.bid_volume],
                'ask_price': [float(p) for p in quote.ask_price],
                'ask_volume': [int(v) for v in quote.ask_volume]
            }
            
            # 更新数据缓存
            self._update_data_cache(symbol, quote_data)
            
            # 通知观察者
            await self._notify_observers(symbol, quote_data)
            
        except Exception as e:
            self.logger.error(f"处理{symbol}的报价数据时发生错误: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            
    async def _on_trade(self, symbol: str, trade: Any) -> None:
        """
        处理逐笔成交数据
        
        Args:
            symbol: 股票代码
            trade: 成交数据
        """
        try:
            if not trade:
                self.logger.warning(f"收到{symbol}的空成交数据")
                return
                
            # 转换数据格式
            trade_data = {
                'symbol': symbol,
                'timestamp': datetime.now(),
                'price': float(trade.price),
                'volume': int(trade.volume),
                'turnover': float(trade.turnover),
                'side': str(trade.side)
            }
            
            # 更新数据缓存
            self._update_data_cache(symbol, trade_data, data_type='trade')
            
            # 通知观察者
            await self._notify_observers(symbol, "Trade", trade_data)
            
        except Exception as e:
            self.logger.error(f"处理{symbol}的成交数据时发生错误: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            
    def _update_data_cache(self, symbol: str, data: Dict[str, Any], data_type: str = 'quote') -> None:
        """
        更新数据缓存
        
        Args:
            symbol: 股票代码
            data: 数据字典
            data_type: 数据类型
        """
        try:
            if symbol not in self.data_cache:
                self.data_cache[symbol] = {'quote': [], 'trade': []}
                
            cache = self.data_cache[symbol][data_type]
            cache.append(data)
            
            # 保持缓存大小
            max_cache_size = self.config.get('max_cache_size', 1000)
            if len(cache) > max_cache_size:
                cache.pop(0)
                
        except Exception as e:
            self.logger.error(f"更新{symbol}的{data_type}数据缓存时发生错误: {str(e)}")
            
    async def _notify_observers(self, symbol: str, data_type: str, data: Any) -> None:
        """通知观察者"""
        try:
            if data_type in self.user_callbacks:
                for callback in self.user_callbacks[data_type]:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(symbol, data)
                        else:
                            # 对于同步回调，使用事件循环来执行
                            self._loop.call_soon_threadsafe(callback, symbol, data)
                    except Exception as e:
                        self.logger.error(f"执行回调函数失败: {str(e)}")
                        self.logger.error(f"Traceback: {traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"通知观察者失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
    
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
        # 等待所有异步任务完成
        if self._tasks:
            self.logger.info(f"等待 {len(self._tasks)} 个异步任务完成...")
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
            
        if self.quote_ctx:
            try:
                # 取消所有订阅
                if self.subscribed_symbols:
                    symbols = list(self.subscribed_symbols)
                    self.logger.info(f"取消所有订阅: {symbols}")
                    try:
                        # 提供订阅类型参数
                        from longport.openapi import SubType
                        sub_types = [SubType.Quote, SubType.Depth, SubType.Brokers, SubType.Trade]
                        self.quote_ctx.unsubscribe(symbols, sub_types)
                    except Exception as e:
                        self.logger.warning(f"取消订阅失败: {e}")
                
                # 关闭上下文
                self.logger.info("关闭行情上下文")
                # SDK的QuoteContext没有提供close方法，只需要设置为None
                self.quote_ctx = None
            except Exception as e:
                self.logger.error(f"关闭行情上下文失败: {e}")
                raise
        else:
            self.logger.debug("行情上下文未初始化，无需关闭")

    def get_cached_quotes(self, symbol: str, lookback_minutes: int = 5) -> List[Dict[str, Any]]:
        """
        获取缓存的行情数据
        
        Args:
            symbol: 股票代码
            lookback_minutes: 回溯时间（分钟）
            
        Returns:
            缓存的行情数据列表
        """
        if symbol not in self.quote_cache:
            return []
            
        cutoff_time = datetime.now() - timedelta(minutes=lookback_minutes)
        return [
            data for time, data in self.quote_cache[symbol].items()
            if time >= cutoff_time
        ]
        
    def get_data_quality(self, symbol: str) -> Dict[str, Any]:
        """
        获取数据质量指标
        
        Args:
            symbol: 股票代码
            
        Returns:
            数据质量指标字典
        """
        return self.data_quality.get(symbol, {})
        
    def is_data_stale(self, symbol: str, max_delay_seconds: int = 60) -> bool:
        """
        检查数据是否过期
        
        Args:
            symbol: 股票代码
            max_delay_seconds: 最大允许延迟（秒）
            
        Returns:
            数据是否过期
        """
        if symbol not in self.last_update_time:
            return True
            
        delay = (datetime.now() - self.last_update_time[symbol]).total_seconds()
        return delay > max_delay_seconds
        
    def get_latest_signals(self) -> Dict[str, Dict[str, Any]]:
        """获取最新的交易信号
        
        Returns:
            包含最新交易信号的字典，格式为 {symbol: {"quote": quote_data}}
        """
        signals = {}
        for symbol, quote in self.latest_quotes.items():
            signals[symbol] = {"quote": quote}
        return signals
