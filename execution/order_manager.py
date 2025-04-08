#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Union, Any, Callable
from enum import Enum
from decimal import Decimal
import os
import time
import json
import uuid
import logging

from utils import ConfigLoader, setup_logger, setup_longport_env
from strategy.signals import Signal, SignalType
from longport.openapi import (
    Config, TradeContext, 
    OrderSide, OrderType, TimeInForceType,
    OrderStatus
)

# 尝试导入长桥API，如果失败则使用模拟实现
try:
    from longport.openapi import (
        Config, 
        TradeContext, 
        OrderType, 
        OrderSide, 
        OrderStatus,
        TimeInForceType
    )
    HAS_LONGPORT = True
except ImportError:
    HAS_LONGPORT = False
    # 模拟枚举
    class OrderType(Enum):
        LO = "LO"  # 限价单
        ELO = "ELO"  # 增强限价单
        MO = "MO"  # 市价单
    
    class OrderSide(Enum):
        Buy = "Buy"  # 买入
        Sell = "Sell"  # 卖出
    
    class OrderStatus(Enum):
        NotReported = "NotReported"  # 未报
        ReportedNotFilled = "ReportedNotFilled"  # 已报未成交
        PartiallyFilled = "PartiallyFilled"  # 部分成交
        Filled = "Filled"  # 全部成交
        Canceled = "Canceled"  # 已撤单
        Rejected = "Rejected"  # 已拒绝
        CancelSubmitted = "CancelSubmitted"  # 已申请撤单
        PartiallyFilledCanceled = "PartiallyFilledCanceled"  # 部分成交已撤单
    
    class TimeInForceType(Enum):
        Day = "Day"  # 当日有效
        GTC = "GTC"  # 撤单前有效

# 模拟订单信息类
class OrderInfo:
    """模拟订单信息类"""
    
    def __init__(self, 
                 order_id: str,
                 symbol: str,
                 side: OrderSide,
                 quantity: Decimal,
                 executed_quantity: Decimal = Decimal("0"),
                 executed_price: Optional[Decimal] = None,
                 status: OrderStatus = OrderStatus.NotReported,
                 submitted_at: datetime = None):
        self.order_id = order_id
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.executed_quantity = executed_quantity
        self.executed_price = executed_price
        self.status = status
        self.submitted_at = submitted_at or datetime.now()

# 模拟持仓类
class Position:
    """模拟持仓类"""
    
    def __init__(self, symbol: str, quantity: Decimal, quantity_for_sale: Decimal = Decimal("0")):
        self.symbol = symbol
        self.quantity = quantity
        self.quantity_for_sale = quantity_for_sale

# 模拟账户余额类
class AccountBalance:
    """模拟账户余额类"""
    
    class CashInfo:
        def __init__(self, currency: str, available: Decimal, frozen: Decimal = Decimal("0")):
            self.currency = currency
            self.available = available
            self.frozen = frozen
    
    def __init__(self):
        self.cash = {
            "HKD": self.CashInfo("HKD", Decimal("100000")),
            "USD": self.CashInfo("USD", Decimal("10000"))
        }
        self.net_assets = Decimal("110000")

# 模拟交易上下文
class MockTradeContext:
    """模拟交易上下文"""
    
    def __init__(self, config):
        self.config = config
        self.orders = {}
        self.positions = {
            "700.HK": Position("700.HK", Decimal("1000")),
            "9988.HK": Position("9988.HK", Decimal("500")),
            "AAPL.US": Position("AAPL.US", Decimal("200"))
        }
        self.balance = AccountBalance()
    
    def submit_order(self, symbol, order_type, side, submitted_quantity, time_in_force, submitted_price, remark):
        """提交订单"""
        order_id = f"O-{uuid.uuid4().hex[:8].upper()}"
        
        # 创建订单
        order = OrderInfo(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=submitted_quantity,
            status=OrderStatus.ReportedNotFilled
        )
        
        # 保存订单
        self.orders[order_id] = order
        
        # 返回订单结果
        class OrderResponse:
            def __init__(self, order_id):
                self.order_id = order_id
        
        return OrderResponse(order_id)
    
    def cancel_order(self, order_id):
        """取消订单"""
        if order_id in self.orders:
            self.orders[order_id].status = OrderStatus.Canceled
        
        class CancelResponse:
            pass
        
        return CancelResponse()
    
    def order_detail(self, order_id):
        """获取订单详情"""
        if order_id in self.orders:
            return self.orders[order_id]
        return None
    
    def today_orders(self):
        """获取今日订单"""
        return list(self.orders.values())
    
    def account_balance(self):
        """获取账户余额"""
        return self.balance
    
    def stock_positions(self):
        """获取持仓"""
        return list(self.positions.values())
    
    def close(self):
        """关闭上下文"""
        pass

class OrderResult:
    """订单结果类"""
    
    def __init__(
        self, 
        order_id: str, 
        symbol: str, 
        side: OrderSide, 
        quantity: int, 
        price: Decimal,
        status: OrderStatus,
        submitted_at: datetime,
        updated_at: Optional[datetime] = None,
        executed_quantity: int = 0,
        executed_price: Optional[Decimal] = None,
        msg: str = ""
    ):
        """
        初始化订单结果
        
        Args:
            order_id: 订单ID
            symbol: 股票代码
            side: 买卖方向
            quantity: 数量
            price: 价格
            status: 订单状态
            submitted_at: 提交时间
            updated_at: 更新时间
            executed_quantity: 成交数量
            executed_price: 成交价格
            msg: 附加消息
        """
        self.order_id = order_id
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.price = price
        self.status = status
        self.submitted_at = submitted_at
        self.updated_at = updated_at or submitted_at
        self.executed_quantity = executed_quantity
        self.executed_price = executed_price
        self.msg = msg
        
    def update_from_order_info(self, info: OrderInfo):
        """根据订单信息更新状态"""
        self.status = info.status
        self.updated_at = datetime.now()
        self.executed_quantity = int(info.executed_quantity)
        self.executed_price = info.executed_price
        
    def is_filled(self) -> bool:
        """是否已成交"""
        return self.status == OrderStatus.Filled
        
    def is_canceled(self) -> bool:
        """是否已取消"""
        return self.status == OrderStatus.Canceled
        
    def is_rejected(self) -> bool:
        """是否被拒绝"""
        return self.status == OrderStatus.Rejected
        
    def is_active(self) -> bool:
        """是否活跃状态"""
        return not (self.is_filled() or self.is_canceled() or self.is_rejected())
        
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.name,
            "quantity": self.quantity,
            "price": float(self.price),
            "status": self.status.name,
            "submitted_at": self.submitted_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "executed_quantity": self.executed_quantity,
            "executed_price": float(self.executed_price) if self.executed_price else None,
            "msg": self.msg
        }
        
    def __str__(self) -> str:
        """转换为字符串"""
        return (
            f"OrderResult(id={self.order_id}, {self.symbol}, {self.side.name}, "
            f"quantity={self.quantity}, price={self.price}, status={self.status.name}, "
            f"executed={self.executed_quantity}/{self.quantity})"
        )

class OrderManager:
    """订单管理器，负责执行交易信号并管理订单生命周期"""
    
    def __init__(self, config_loader: ConfigLoader):
        """
        初始化订单管理器
        
        Args:
            config_loader: 配置加载器
        """
        self.config = config_loader
        
        # 设置日志
        self.logger = setup_logger(
            "order_manager", 
            self.config.get("logging.level", "INFO"), 
            self.config.get("logging.file")
        )
        
        # 初始化LongPort API环境
        setup_longport_env()
        
        # 创建长桥API配置
        self.logger.info("创建长桥API配置")
        self.longport_config = Config.from_env()
        
        # 初始化交易上下文
        self.trade_ctx = None
        
        # 记录订单
        self.active_orders = {}  # order_id -> OrderResult
        self.filled_orders = {}  # order_id -> OrderResult
        self.canceled_orders = {}  # order_id -> OrderResult
        self.rejected_orders = {}  # order_id -> OrderResult
        
        # 记录今日订单数量和总交易金额
        self.daily_order_count = 0
        self.daily_total_amount = Decimal("0")
        
        # 最大持仓大小（按股数）
        self.max_position_size = self.config.get("execution.max_position_size", 10000)
        
        # 最大订单数量
        self.max_daily_orders = self.config.get("execution.max_daily_orders", 50)
        
        # 最大仓位比例（占账户总资产的百分比）
        self.max_position_pct = self.config.get("execution.risk_control.position_pct", 5.0)
        
        # 订单回调函数
        self.order_callbacks = []
        
    async def initialize(self):
        """初始化交易上下文"""
        # 如果已经初始化，则直接返回
        if self.trade_ctx:
            return
        
        self.logger.info("正在初始化交易上下文...")
        
        # 重试逻辑
        max_retries = 3
        retry_delay = 5  # 秒
        
        for attempt in range(1, max_retries + 1):
            try:
                self.logger.info(f"尝试创建交易上下文 (尝试 {attempt}/{max_retries})...")
                
                # 创建交易上下文
                self.trade_ctx = TradeContext(self.longport_config)
                
                # 验证连接 - 尝试获取账户余额来验证连接是否成功
                try:
                    balance = self.trade_ctx.account_balance()
                    self.logger.info(f"成功获取账户余额，API连接正常")
                except Exception as e:
                    self.logger.warning(f"获取账户余额失败，但上下文已创建: {e}")
                
                self.logger.info("交易上下文初始化完成")
                return
            except Exception as e:
                self.logger.error(f"交易上下文初始化失败 (尝试 {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    self.logger.info(f"等待 {retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                else:
                    self.logger.error("已达到最大重试次数，无法初始化交易上下文")
                    raise

    # 处理信号的方法
    async def process_signal(self, signal: Signal) -> Optional[OrderResult]:
        """处理信号并执行交易"""
        return await self.execute_signal(signal)
        
    def register_order_callback(self, callback: Callable[[OrderResult], None]):
        """
        注册订单回调函数
        
        Args:
            callback: 回调函数，接收OrderResult参数
        """
        self.order_callbacks.append(callback)
        self.logger.debug(f"已注册订单回调函数: {callback.__name__}")
    
    async def execute_signal(self, signal: Signal) -> Optional[OrderResult]:
        """
        执行交易信号
        
        Args:
            signal: 交易信号
            
        Returns:
            订单结果，如果没有执行则返回None
        """
        if signal.signal_type == SignalType.HOLD:
            self.logger.info(f"信号为HOLD，不执行交易: {signal}")
            return None
            
        # 检查是否满足风控条件
        if not self._validate_risk_control(signal):
            self.logger.warning(f"信号不满足风控条件，不执行交易: {signal}")
            return None
            
        # 执行交易
        if signal.signal_type == SignalType.BUY:
            return await self._execute_buy(signal)
        elif signal.signal_type == SignalType.SELL:
            return await self._execute_sell(signal)
        else:
            self.logger.warning(f"未知的信号类型: {signal.signal_type}")
            return None
    
    async def _execute_buy(self, signal: Signal) -> Optional[OrderResult]:
        """
        执行买入交易
        
        Args:
            signal: 买入信号
            
        Returns:
            订单结果
        """
        self.logger.info(f"执行买入: {signal}")
        
        # 验证账户余额
        try:
            # 添加await关键字
            balance = await self.get_account_balance()
            if not balance:
                self.logger.error("无法获取账户余额，取消买入")
                return None
                
            # 验证资金是否足够
            # 这里假设第一个币种为交易币种
            currency = list(balance.cash.keys())[0]
            available = float(balance.cash[currency].available)
            required = signal.price * signal.quantity
            
            if available < required:
                self.logger.warning(f"资金不足: 可用{available}，需要{required}，减少交易数量")
                # 按照可用资金比例减少数量
                adjusted_quantity = int(available / signal.price * 0.9)  # 留10%余量
                if adjusted_quantity <= 0:
                    self.logger.error(f"调整后的数量为0，取消买入")
                    return None
                    
                self.logger.info(f"调整买入数量: {signal.quantity} -> {adjusted_quantity}")
                signal.quantity = adjusted_quantity
        except Exception as e:
            self.logger.error(f"验证账户余额出错: {e}")
            
        # 提交买入订单
        result = await self._submit_order(
            symbol=signal.symbol,
            side=OrderSide.Buy,
            quantity=signal.quantity,
            price=signal.price
        )
        
        # 更新今日订单计数
        if result:
            self.daily_order_count += 1
            
        return result
    
    async def _execute_sell(self, signal: Signal) -> Optional[OrderResult]:
        """
        执行卖出交易
        
        Args:
            signal: 卖出信号
            
        Returns:
            订单结果
        """
        self.logger.info(f"执行卖出: {signal}")
        
        # 验证持仓
        try:
            # 添加await关键字
            positions = await self.get_positions()
            position = next((p for p in positions if p.symbol == signal.symbol), None)
            
            if not position:
                self.logger.warning(f"没有 {signal.symbol} 的持仓，取消卖出")
                return None
                
            # 验证持仓数量是否足够
            available = int(position.quantity - position.quantity_for_sale)
            if available < signal.quantity:
                self.logger.warning(f"持仓不足: 可用{available}，需要{signal.quantity}，减少交易数量")
                signal.quantity = available
                
            if signal.quantity <= 0:
                self.logger.error(f"调整后的数量为0，取消卖出")
                return None
        except Exception as e:
            self.logger.error(f"验证持仓出错: {e}")
            
        # 提交卖出订单
        result = await self._submit_order(
            symbol=signal.symbol,
            side=OrderSide.Sell,
            quantity=signal.quantity,
            price=signal.price
        )
        
        # 更新今日订单计数
        if result:
            self.daily_order_count += 1
            
        return result
                
    async def _submit_order(
        self, 
        symbol: str, 
        side: OrderSide, 
        quantity: int, 
        price: float
    ) -> Optional[OrderResult]:
        """
        提交订单
        
        Args:
            symbol: 股票代码
            side: 买卖方向
            quantity: 数量
            price: 价格
            
        Returns:
            订单结果
        """
        if not self.trade_ctx:
            await self.initialize()
            
        self.logger.info(f"提交订单: {symbol}, {side.name}, quantity={quantity}, price={price}")
        
        try:
            # 默认订单类型
            order_type_str = self.config.get("execution.order_types.default", "LO")
            order_type = getattr(OrderType, order_type_str)
            
            # 提交订单
            # 添加await关键字
            response = await self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=order_type,
                side=side,
                submitted_quantity=Decimal(str(quantity)),
                time_in_force=TimeInForceType.Day,
                submitted_price=Decimal(str(price)),
                remark="Generated by LSTM model"
            )
            
            # 创建订单结果
            result = OrderResult(
                order_id=response.order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=Decimal(str(price)),
                status=OrderStatus.NotReported,
                submitted_at=datetime.now()
            )
            
            # 保存订单
            self.active_orders[result.order_id] = result
            
            # 调用回调函数
            for callback in self.order_callbacks:
                try:
                    callback(result)
                except Exception as e:
                    self.logger.error(f"执行订单回调函数出错: {e}")
            
            self.logger.info(f"订单已提交: {result}")
            return result
        except Exception as e:
            self.logger.error(f"提交订单失败: {e}")
            return None
    
    async def cancel_order(self, order_id: str) -> bool:
        """
        取消订单
        
        Args:
            order_id: 订单ID
            
        Returns:
            是否取消成功
        """
        if not self.trade_ctx:
            await self.initialize()
            
        if order_id not in self.active_orders:
            self.logger.warning(f"订单ID不存在: {order_id}")
            return False
            
        order = self.active_orders[order_id]
        if not order.is_active():
            self.logger.warning(f"订单不是活跃状态，无法取消: {order}")
            return False
            
        self.logger.info(f"取消订单: {order_id}")
        
        try:
            # 添加await关键字
            response = await self.trade_ctx.cancel_order(order_id)
            
            # 更新订单状态
            order.status = OrderStatus.CancelSubmitted
            order.updated_at = datetime.now()
            order.msg = "Cancellation submitted"
            
            # 调用回调函数
            for callback in self.order_callbacks:
                try:
                    callback(order)
                except Exception as e:
                    self.logger.error(f"执行订单回调函数出错: {e}")
            
            return True
        except Exception as e:
            self.logger.error(f"取消订单失败: {e}")
            return False
    
    async def get_order_status(self, order_id: str) -> Optional[OrderResult]:
        """
        获取订单状态
        
        Args:
            order_id: 订单ID
            
        Returns:
            更新后的订单结果
        """
        if not self.trade_ctx:
            await self.initialize()
            
        if order_id not in self.active_orders:
            self.logger.warning(f"订单ID不存在: {order_id}")
            return None
            
        try:
            # 添加await关键字
            order_info = await self.trade_ctx.order_detail(order_id)
            
            # 更新订单状态
            order = self.active_orders[order_id]
            order.update_from_order_info(order_info)
            
            # 调用回调函数
            for callback in self.order_callbacks:
                try:
                    callback(order)
                except Exception as e:
                    self.logger.error(f"执行订单回调函数出错: {e}")
            
            return order
        except Exception as e:
            self.logger.error(f"获取订单状态失败: {e}")
            return self.active_orders[order_id]
    
    async def get_today_orders(self) -> List[OrderResult]:
        """
        获取今日订单
        
        Returns:
            今日订单列表
        """
        if not self.trade_ctx:
            await self.initialize()
            
        try:
            # 添加await关键字
            today_orders = await self.trade_ctx.today_orders()
            
            # 更新本地订单缓存
            for order_info in today_orders:
                order_id = order_info.order_id
                
                if order_id in self.active_orders:
                    # 更新已有订单
                    self.active_orders[order_id].update_from_order_info(order_info)
                else:
                    # 创建新订单
                    order = OrderResult(
                        order_id=order_id,
                        symbol=order_info.symbol,
                        side=order_info.side,
                        quantity=int(order_info.quantity),
                        price=order_info.submitted_price,
                        status=order_info.status,
                        submitted_at=order_info.submitted_at,
                        executed_quantity=int(order_info.executed_quantity),
                        executed_price=order_info.executed_price
                    )
                    self.active_orders[order_id] = order
            
            # 统计今日订单数
            self.daily_order_count = len(today_orders)
            
            return list(self.active_orders.values())
        except Exception as e:
            self.logger.error(f"获取今日订单失败: {e}")
            return list(self.active_orders.values())
    
    async def get_account_balance(self):
        """
        获取账户余额
        
        Returns:
            账户余额数据
        """
        if not self.trade_ctx:
            await self.initialize()
            
        try:
            # LongPort API 的 account_balance 方法不是异步的，不需要 await
            balance = self.trade_ctx.account_balance()
            self.logger.debug(f"获取账户余额成功")
            return balance
        except Exception as e:
            self.logger.error(f"获取账户余额失败: {e}")
            return None
    
    async def get_positions(self):
        """
        获取持仓
        
        Returns:
            持仓数据
        """
        if not self.trade_ctx:
            await self.initialize()
            
        try:
            # LongPort API 的 stock_positions 方法不是异步的，不需要 await
            positions = self.trade_ctx.stock_positions()
            self.logger.debug(f"获取持仓成功: {len(positions) if positions else 0}个股票")
            return positions
        except Exception as e:
            self.logger.error(f"获取持仓失败: {e}")
            return None
    
    def _validate_risk_control(self, signal: Signal) -> bool:
        """
        验证风控条件
        
        Args:
            signal: 交易信号
            
        Returns:
            是否通过风控检查
        """
        # 订单数量限制
        if signal.quantity > self.max_position_size:
            self.logger.warning(f"订单数量超过限制: {signal.quantity} > {self.max_position_size}")
            signal.quantity = self.max_position_size
            
        # 今日订单数限制
        if self.daily_order_count >= self.max_daily_orders:
            self.logger.warning(f"今日订单数已达上限: {self.daily_order_count} >= {self.max_daily_orders}")
            return False
            
        return True
    
    async def close(self):
        """关闭交易上下文"""
        if self.trade_ctx:
            self.logger.info("关闭交易上下文")
            self.trade_ctx.close()
            self.trade_ctx = None
