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
import csv
import traceback

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
    """
    订单结果对象
    """
    
    def __init__(self, 
                 order_id: str, 
                 symbol: str, 
                 side: OrderSide, 
                 quantity: int, 
                 price: float, 
                 status: OrderStatus,
                 submitted_at: datetime,
                 msg: str = "",
                 strategy_name: str = ""):
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
            msg: 消息（通常用于错误信息）
            strategy_name: 策略名称
        """
        self.order_id = order_id
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.price = price
        self.status = status
        self.submitted_at = submitted_at
        self.filled_quantity = 0
        self.avg_price = 0.0
        self.last_updated = submitted_at
        self.msg = msg
        self.strategy_name = strategy_name
        
    def update_from_order_info(self, info: OrderInfo):
        """根据订单信息更新状态"""
        self.status = info.status
        self.last_updated = datetime.now()
        self.filled_quantity = int(info.executed_quantity)
        self.avg_price = float(info.executed_price) if info.executed_price else 0.0
        
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
        # 安全获取side的name属性
        side_name = self.side.name if hasattr(self.side, 'name') else str(self.side)
        # 安全获取status的name属性
        status_name = self.status.name if hasattr(self.status, 'name') else str(self.status)
        
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": side_name,
            "quantity": self.quantity,
            "price": float(self.price),
            "status": status_name,
            "submitted_at": self.submitted_at.isoformat(),
            "updated_at": self.last_updated.isoformat(),
            "executed_quantity": self.filled_quantity,
            "executed_price": float(self.avg_price) if self.avg_price else None,
            "msg": self.msg,
            "strategy_name": self.strategy_name
        }
        
    def __str__(self) -> str:
        """转换为字符串"""
        # 安全获取side和status的name属性
        side_name = self.side.name if hasattr(self.side, 'name') else str(self.side)
        status_name = self.status.name if hasattr(self.status, 'name') else str(self.status)
        
        return (
            f"OrderResult(id={self.order_id}, {self.symbol}, {side_name}, "
            f"quantity={self.quantity}, price={self.price}, status={status_name}, "
            f"executed={self.filled_quantity}/{self.quantity})"
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
        self.daily_orders_count = 0  # 用于实际计数的变量
        self.daily_total_amount = Decimal("0")
        
        # 最大持仓大小（按股数）- 确保是整数类型
        self.max_position_size = int(self.config.get("execution.max_position_size", 10000))
        
        # 最大订单数量 - 确保是整数类型
        self.max_daily_orders = int(self.config.get("execution.max_daily_orders", 50))
        
        # 最大仓位比例（占账户总资产的百分比）
        self.max_position_pct = float(self.config.get("execution.risk_control.position_pct", 5.0))
        
        # 单笔最大订单数量 - 确保是整数类型
        self.max_order_size = int(self.config.get("execution.max_order_size", 5000))
        
        # 交易上下文初始化标志
        self._trade_ctx_initialized = False
        
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
    
    async def execute_signal(self, signal: Signal):
        """执行交易信号"""
        if not self._validate_risk_control(signal):
            self.logger.warning(f"风控检查不通过，拒绝执行信号: {signal}")
            return None
        
        try:
            self.logger.info(f"执行交易信号: {signal}")
            symbol = signal.symbol
            
            # 获取股票的市场信息，包括最小交易单位（批量）
            lot_size = self.get_lot_size(symbol)
            if lot_size <= 0:
                self.logger.error(f"无法获取股票 {symbol} 的最小交易单位")
                return None
            
            # 确保交易数量是最小交易单位的整数倍
            adjusted_quantity = (signal.quantity // lot_size) * lot_size
            if adjusted_quantity <= 0:
                adjusted_quantity = lot_size  # 至少买入一个最小交易单位
            
            if adjusted_quantity != signal.quantity:
                self.logger.info(f"调整交易数量从 {signal.quantity} 到 {adjusted_quantity} 以符合最小交易单位 {lot_size}")
            
            # 创建订单
            if signal.signal_type == SignalType.BUY:
                result = await self._create_buy_order(signal)
            else:  # SELL
                result = await self._create_sell_order(signal)
            
            if result:
                self.logger.info(f"订单已提交: {result}")
                # 保存订单
                self._save_order(result, signal)
                return result
            else:
                self.logger.error(f"订单提交失败: {symbol}, {signal.signal_type}, {adjusted_quantity}")
                return None
        except Exception as e:
            self.logger.error(f"执行信号失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    async def _create_buy_order(self, signal: Signal) -> OrderResult:
        """
        创建买入订单
        
        Args:
            signal: 买入信号
            
        Returns:
            OrderResult: 订单结果
        """
        symbol = signal.symbol
        price = signal.price
        quantity = signal.quantity
        
        # 检查日内最大订单数量
        if not self._check_daily_order_limit():
            self.logger.warning(f"达到日内最大订单数量限制，拒绝买入信号: {signal}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg="达到日内最大订单数量限制",
                strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            )
        
        # 获取可用资金
        available_cash = self.get_account_balance()
        if available_cash <= 0:
            self.logger.warning(f"可用资金不足，无法买入: {symbol}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy, 
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg="可用资金不足",
                strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            )
        
        # 计算所需资金（加上一些手续费的缓冲）
        required_cash = price * quantity * 1.01  # 假设1%的手续费缓冲
        
        # 检查资金是否足够
        if required_cash > available_cash:
            self.logger.warning(f"可用资金不足，调整买入数量: {symbol}, 原始数量: {quantity}, 可用资金: {available_cash}, 所需资金: {required_cash}")
            
            # 计算可买数量
            affordable_quantity = int(available_cash / (price * 1.01))
            if affordable_quantity <= 0:
                self.logger.warning(f"即使调整后可买数量仍为0，拒绝买入: {symbol}")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.Buy,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.Rejected,
                    submitted_at=datetime.now(),
                    msg="可用资金不足以买入最小单位",
                    strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                )
            
            # 调整为整手
            affordable_quantity = self._adjust_lot_size(symbol, affordable_quantity)
            if affordable_quantity <= 0:
                self.logger.warning(f"调整为整手后可买数量为0，拒绝买入: {symbol}")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.Buy,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.Rejected,
                    submitted_at=datetime.now(),
                    msg="可用资金不足以买入最小单位",
                    strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                )
            
            # 更新数量
            self.logger.info(f"调整买入数量: {quantity} -> {affordable_quantity}")
            quantity = affordable_quantity
        else:
            # 调整为整手
            quantity = self._adjust_lot_size(symbol, quantity)
        
        # 检查单笔最大数量限制
        if quantity > self.max_order_size:
            self.logger.warning(f"买入数量超过单笔最大限制，调整为最大限制: {quantity} -> {self.max_order_size}")
            quantity = self._adjust_lot_size(symbol, self.max_order_size)
        
        # 检查持仓限制
        current_position = self.get_positions(symbol)
        current_quantity = sum(position.quantity for position in current_position if hasattr(position, 'quantity'))
        
        if current_quantity + quantity > self.max_position_size:
            adjusted_quantity = self.max_position_size - current_quantity
            if adjusted_quantity <= 0:
                self.logger.warning(f"持仓数量已达上限，拒绝买入: {symbol}")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.Buy,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.Rejected, 
                    submitted_at=datetime.now(),
                    msg="持仓数量已达上限",
                    strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                )
            
            # 调整为整手
            adjusted_quantity = self._adjust_lot_size(symbol, adjusted_quantity)
            if adjusted_quantity <= 0:
                self.logger.warning(f"调整持仓限制后数量为0，拒绝买入: {symbol}")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.Buy,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.Rejected,
                    submitted_at=datetime.now(),
                    msg="无法购买最小单位",
                    strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                )
            
            self.logger.warning(f"调整买入数量以符合持仓限制: {quantity} -> {adjusted_quantity}")
            quantity = adjusted_quantity
        
        # 创建买入订单
        if quantity <= 0:
            self.logger.warning(f"最终买入数量不合法，拒绝买入: {symbol}, 数量: {quantity}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg="调整后数量不合法",
                strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            )
        
        # 提交订单
        return await self._submit_order(symbol, price, quantity, "buy", signal.strategy_name if hasattr(signal, 'strategy_name') else "")

    async def _create_sell_order(self, signal):
        """
        创建卖出订单

        Args:
            signal: 交易信号

        Returns:
            OrderResult: 订单结果
        """
        symbol = signal.symbol
        price = signal.price
        quantity = signal.quantity
        
        # 检查日内订单是否达到上限
        if not self._check_daily_order_limit():
            self.logger.warning(f"达到日内最大订单数量限制: {self.daily_order_count}/{self.max_daily_orders}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Sell,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg="达到日内最大订单数量限制",
                strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            )
        
        # 检查持仓
        positions = self.get_positions()
        position = next((p for p in positions if p.symbol.lower() == symbol.lower()), None)
        
        if not position or position.quantity < quantity:
            self.logger.warning(f"持仓不足: {symbol}, 需要: {quantity}, 实际: {position.quantity if position else 0}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Sell,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=f"持仓不足，需要: {quantity}, 实际: {position.quantity if position else 0}",
                strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            )
        
        # 调整批量大小
        adjusted_quantity = self._adjust_lot_size(symbol, quantity)
        
        if adjusted_quantity <= 0:
            self.logger.warning(f"调整后数量不合法: {symbol}, 原始数量: {quantity}, 调整后: {adjusted_quantity}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Sell,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg="调整后数量不合法",
                strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            )
        
        # 提交订单
        return await self._submit_order(symbol, price, adjusted_quantity, "sell", signal.strategy_name if hasattr(signal, 'strategy_name') else "")
                
    async def _submit_order(self, symbol: str, price: float, quantity: int, order_type: str, strategy_name: str) -> OrderResult:
        """
        提交订单到经纪商

        Args:
            symbol: 股票代码
            price: 价格
            quantity: 数量
            order_type: 订单类型，'BUY'或'SELL'
            strategy_name: 策略名称

        Returns:
            OrderResult: 订单结果对象
        """
        # 初始化交易上下文（如果尚未初始化）
        if not self._trade_ctx_initialized:
            if not await self._init_trade_context():
                self.logger.error(f"无法提交订单 {symbol}: 交易上下文初始化失败")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.Buy if order_type == "BUY" else OrderSide.Sell,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.Rejected,
                    submitted_at=datetime.now(),
                    msg="交易上下文初始化失败",
                    strategy_name=strategy_name
                )
        
        # 转换订单类型
        side = OrderSide.Buy if order_type == "BUY" else OrderSide.Sell
        
        # 记录订单提交
        self.logger.info(f"提交{order_type}订单: {symbol} x {quantity} @ {price} [{strategy_name}]")
        
        # 增加每日订单计数
        self.daily_orders_count += 1
        
        try:
            # 提交订单
            submitted_time = datetime.now()
            response = self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.LO,  # 限价单
                side=side,
                submitted_price=price,
                submitted_quantity=quantity,
                time_in_force=TimeInForceType.Day,
                remark=f"Strategy: {strategy_name}"
            )
            
            if not response or not response.order_id:
                self.logger.error(f"订单提交失败: {symbol} - API响应无效")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.Rejected,
                    submitted_at=submitted_time,
                    msg="订单提交失败: API响应无效",
                    strategy_name=strategy_name
                )
                
            # 创建并返回订单结果
            order_result = OrderResult(
                order_id=response.order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                status=OrderStatus.New,
                submitted_at=submitted_time,
                strategy_name=strategy_name
            )
            
            # 添加到活跃订单列表
            self.active_orders[response.order_id] = order_result
            self.logger.info(f"订单已提交: ID={response.order_id}, {symbol}, {order_type}, {quantity}@{price}")
            
            return order_result
            
        except Exception as e:
            import traceback
            self.logger.error(f"订单提交异常: {symbol} - {str(e)}")
            self.logger.error(traceback.format_exc())
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=f"订单提交异常: {str(e)}",
                strategy_name=strategy_name
            )
    
    def _adjust_lot_size(self, symbol: str, quantity: int) -> int:
        """
        调整股票手数，确保符合最小交易单位
        
        Args:
            symbol: 股票代码
            quantity: 原始数量
            
        Returns:
            调整后的数量
        """
        if not self.trade_ctx:
            try:
                # 同步初始化
                if hasattr(self, 'initialize'):
                    self.initialize()
            except Exception as e:
                self.logger.error(f"初始化交易上下文失败: {e}")
            
        try:
            # 获取股票交易信息
            lot_size = self.get_lot_size(symbol)
            self.logger.debug(f"{symbol} 获取到手数: {lot_size}")
                
            # 调整为整手数量
            if lot_size > 0:
                adjusted_quantity = (quantity // lot_size) * lot_size
                if adjusted_quantity == 0 and quantity > 0:
                    adjusted_quantity = lot_size
                
                if adjusted_quantity != quantity:
                    self.logger.info(f"调整交易数量以符合最小交易单位: {quantity} -> {adjusted_quantity}")
                
                return adjusted_quantity
            else:
                self.logger.warning(f"无法获取正确的手数信息，使用原始数量: {quantity}")
                return quantity
        except Exception as e:
            self.logger.warning(f"调整交易数量出错: {e}，使用原始数量: {quantity}")
            return quantity
            
    def get_lot_size(self, symbol: str) -> int:
        """
        获取股票的最小交易单位
        
        Args:
            symbol: 股票代码
            
        Returns:
            最小交易单位
        """
        try:
            # 根据股票代码判断市场
            if '.HK' in symbol:
                # 港股默认最小手数为100或1000
                lot_size = 100
                self.logger.debug(f"{symbol} 默认使用港股手数: {lot_size}")
            elif '.US' in symbol:
                # 美股默认最小手数为1
                lot_size = 1
                self.logger.debug(f"{symbol} 默认使用美股手数: {lot_size}")
            elif '.SH' in symbol or '.SZ' in symbol:
                # A股默认最小手数为100
                lot_size = 100
                self.logger.debug(f"{symbol} 默认使用A股手数: {lot_size}")
            else:
                # 默认手数为100
                lot_size = 100
                self.logger.debug(f"{symbol} 未知市场，使用默认手数: {lot_size}")
                
            # 尝试从API获取准确的手数信息
            try:
                if self.trade_ctx:
                    # 这里可以添加获取准确手数的API调用
                    # 例如: 
                    # stock_info = self.trade_ctx.stock_info(symbol)
                    # if hasattr(stock_info, 'lot_size'):
                    #     lot_size = stock_info.lot_size
                    #     self.logger.debug(f"{symbol} 从API获取手数: {lot_size}")
                    pass
            except Exception as e:
                self.logger.warning(f"获取{symbol}的手数信息失败: {e}，使用默认手数: {lot_size}")
                
            return lot_size
        except Exception as e:
            self.logger.error(f"获取股票手数出错: {e}")
            # 返回默认值
            return 100
    
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
            # 移除await关键字
            response = self.trade_ctx.cancel_order(order_id)
            
            # 更新订单状态
            order.status = OrderStatus.CancelSubmitted
            order.last_updated = datetime.now()
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
            # 移除await关键字
            order_info = self.trade_ctx.order_detail(order_id)
            
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
    
    async def get_today_orders(self, symbol: str = None):
        """
        获取今日委托
        
        Args:
            symbol: 可选，指定股票代码
            
        Returns:
            今日委托列表
        """
        try:
            orders_response = self.trade_ctx.today_orders()
            
            # TodayOrdersResponse需要通过list属性获取委托列表
            orders = orders_response.list if hasattr(orders_response, 'list') else []
            order_count = len(orders) if orders else 0
            self.logger.info(f"成功获取今日委托, 共{order_count}个")
            
            # 如果指定了股票代码，则过滤委托
            if symbol and orders:
                orders = [o for o in orders if o.symbol.lower() == symbol.lower()]
                self.logger.debug(f"过滤委托 {symbol}, 结果: {len(orders)}个")
                
            return orders
        except Exception as e:
            self.logger.error(f"获取今日委托失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return []
    
    def get_account_balance(self):
        """
        获取账户余额
        
        Returns:
            可用资金总额（浮点数），如果失败则返回0
        """
        try:
            # 获取账户余额
            balance_response = self.trade_ctx.account_balance()
            
            # 对象属性的日志记录，用于调试
            self.logger.debug(f"账户余额对象类型: {type(balance_response)}")
            
            # 处理列表类型的响应 - 长桥SDK最新版本返回的是列表
            if isinstance(balance_response, list):
                self.logger.info(f"账户余额是列表格式，包含 {len(balance_response)} 项")
                
                total_available = 0.0
                
                for item in balance_response:
                    # 获取cash_infos属性，它是一个包含各种货币可用资金的列表
                    if hasattr(item, 'cash_infos') and item.cash_infos:
                        for cash_info in item.cash_infos:
                            if hasattr(cash_info, 'available_cash'):
                                self.logger.info(f"获取到{cash_info.currency}可用资金: {cash_info.available_cash}")
                                # 所有货币都转换为账户的基准货币
                                if cash_info.currency == "USD":
                                    # 美元按1:7.8转换为港币计算(假设账户基准货币是港币)
                                    total_available += float(cash_info.available_cash) * 7.8
                                elif cash_info.currency == "HKD":
                                    # 港币直接加入
                                    total_available += float(cash_info.available_cash)
                                else:
                                    # 其他货币假设已转换为账户基准货币
                                    total_available += float(cash_info.available_cash)
                
                self.logger.info(f"账户总可用资金: {total_available}")
                return total_available
            
            # 检查响应类型
            if hasattr(balance_response, 'cash_infos') and balance_response.cash_infos:
                # 新的API返回格式
                total_available = 0.0
                
                # 记录每种货币的余额
                for cash_info in balance_response.cash_infos:
                    if hasattr(cash_info, 'available_cash'):
                        self.logger.info(f"获取到{cash_info.currency}可用资金: {cash_info.available_cash}")
                        if cash_info.currency == "USD":
                            # 美元按1:7.8转换为港币计算(假设账户基准货币是港币)
                            total_available += float(cash_info.available_cash) * 7.8
                        elif cash_info.currency == "HKD":
                            # 港币直接加入
                            total_available += float(cash_info.available_cash)
                        else:
                            # 其他货币假设已转换为账户基准货币
                            total_available += float(cash_info.available_cash)
                
                self.logger.info(f"账户总可用资金: {total_available}")
                return total_available
            elif hasattr(balance_response, 'list'):
                # 旧API返回格式 - 列表形式
                balances = balance_response.list
                total_available = 0.0
                
                for balance in balances:
                    if hasattr(balance, 'available'):
                        self.logger.info(f"获取到{balance.currency}可用资金: {balance.available}")
                        if balance.currency == "USD":
                            # 美元按1:7.8转换为港币计算(假设账户基准货币是港币)
                            total_available += float(balance.available) * 7.8
                        elif balance.currency == "HKD":
                            # 港币直接加入
                            total_available += float(balance.available)
                        else:
                            # 其他货币假设已转换为账户基准货币
                            total_available += float(balance.available)
                    
                self.logger.info(f"账户总可用资金: {total_available}")
                return total_available
            elif hasattr(balance_response, 'cash') and isinstance(balance_response.cash, dict):
                # 模拟数据格式
                total_available = 0.0
                
                for currency, info in balance_response.cash.items():
                    if hasattr(info, 'available'):
                        self.logger.info(f"获取到{currency}可用资金: {info.available}")
                        total_available += float(info.available)
                
                self.logger.info(f"账户总可用资金: {total_available}")
                return total_available
            else:
                # 未知格式，尝试记录对象属性以便调试
                attrs = dir(balance_response)
                self.logger.warning(f"无法识别的账户余额格式，对象属性: {attrs}")
                
                # 尝试查找可能的余额属性
                if hasattr(balance_response, 'net_assets'):
                    self.logger.info(f"使用net_assets作为可用资金: {balance_response.net_assets}")
                    return float(balance_response.net_assets)
                elif hasattr(balance_response, 'total_cash'):
                    self.logger.info(f"使用total_cash作为可用资金: {balance_response.total_cash}")
                    return float(balance_response.total_cash)
                
                # 如果无法解析，返回默认值
                self.logger.error(f"无法获取账户可用资金，返回默认值")
                return 100000.0  # 返回一个默认值，避免交易被完全阻止
        except Exception as e:
            self.logger.error(f"获取账户余额失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return 0.0
    
    def get_positions(self, symbol: str = None):
        """
        获取当前持仓
        
        Args:
            symbol: 可选，指定股票代码
            
        Returns:
            持仓列表
        """
        try:
            # 注意：stock_positions()方法可能不是异步方法，不需要await
            positions_response = self.trade_ctx.stock_positions()
            
            # StockPositionsResponse需要通过list属性获取持仓列表
            positions = positions_response.list if hasattr(positions_response, 'list') else []
            position_count = len(positions) if positions else 0
            self.logger.info(f"成功获取持仓, 共{position_count}个")
            
            # 如果指定了股票代码，则过滤持仓
            if symbol and positions:
                positions = [p for p in positions if p.symbol.lower() == symbol.lower()]
                self.logger.debug(f"过滤持仓 {symbol}, 结果: {len(positions)}个")
                
            return positions
        except Exception as e:
            self.logger.error(f"获取持仓失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return []
    
    def _validate_risk_control(self, signal: Signal) -> bool:
        """
        验证风控条件
        
        Args:
            signal: 交易信号
            
        Returns:
            是否通过风控检查
        """
        # 确保类型转换
        try:
            signal_quantity = int(signal.quantity)
            max_position_size = int(self.max_position_size)
            daily_order_count = int(self.daily_order_count)
            max_daily_orders = int(self.max_daily_orders)
            
            # 订单数量限制
            if signal_quantity > max_position_size:
                self.logger.warning(f"订单数量超过限制: {signal_quantity} > {max_position_size}")
                signal.quantity = max_position_size
                
            # 今日订单数限制
            if daily_order_count >= max_daily_orders:
                self.logger.warning(f"今日订单数已达上限: {daily_order_count} >= {max_daily_orders}")
                return False
                
            return True
        except Exception as e:
            self.logger.error(f"风控验证时发生类型转换错误: {e}")
            return False
    
    async def close(self):
        """关闭交易上下文"""
        if self.trade_ctx:
            self.logger.info("关闭交易上下文")
            self.trade_ctx.close()
            self.trade_ctx = None

    def _save_order(self, order_result: OrderResult, signal: Signal):
        """保存订单结果"""
        try:
            # 记录信号和订单的关联
            if not hasattr(self, 'signal_order_map'):
                self.signal_order_map = {}
            
            # 生成一个唯一的信号标识符，因为Signal对象没有id属性
            signal_id = f"{signal.symbol}_{signal.signal_type}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{id(signal)}"
            
            self.signal_order_map[signal_id] = order_result.order_id
            
            # 这里可以将订单信息保存到数据库
            # 将订单信息写入CSV文件
            order_data = {
                'order_id': order_result.order_id,
                'symbol': order_result.symbol,
                'side': order_result.side.value if hasattr(order_result.side, 'value') else str(order_result.side),
                'quantity': order_result.quantity,
                'price': order_result.price,
                'status': order_result.status.value if hasattr(order_result.status, 'value') else str(order_result.status),
                'signal_id': signal_id,
                'signal_type': signal.signal_type.value if hasattr(signal.signal_type, 'value') else str(signal.signal_type),
                'submitted_at': order_result.submitted_at.isoformat() if hasattr(order_result, 'submitted_at') and order_result.submitted_at else '',
            }
            
            # 检查是否存在这些属性，并安全添加
            if hasattr(order_result, 'filled_at') and order_result.filled_at:
                order_data['filled_at'] = order_result.filled_at.isoformat()
            else:
                order_data['filled_at'] = ''
                
            if hasattr(order_result, 'cancelled_at') and order_result.cancelled_at:
                order_data['cancelled_at'] = order_result.cancelled_at.isoformat()
            else:
                order_data['cancelled_at'] = ''
                
            if hasattr(order_result, 'rejected_at') and order_result.rejected_at:
                order_data['rejected_at'] = order_result.rejected_at.isoformat()
            else:
                order_data['rejected_at'] = ''
            
            # 确保日志目录存在
            log_dir = self.config.get('logging', {}).get('dir', 'logs')
            os.makedirs(log_dir, exist_ok=True)
            
            orders_csv = os.path.join(log_dir, 'orders.csv')
            file_exists = os.path.isfile(orders_csv)
            
            with open(orders_csv, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=order_data.keys())
                if not file_exists:
                    writer.writeheader()
                writer.writerow(order_data)
            
            self.logger.info(f"订单信息已保存到 {orders_csv}")
        except Exception as e:
            self.logger.error(f"保存订单信息失败: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")

    def _check_daily_order_limit(self) -> bool:
        """
        检查是否达到每日订单数量限制
        
        Returns:
            bool: 如果未达到限制则返回True，否则返回False
        """
        # 获取每日最大订单数量配置
        max_daily_orders = self.max_daily_orders
        
        # 如果每日订单数已达到或超过限制，则返回False
        if self.daily_order_count >= max_daily_orders:
            self.logger.warning(f"已达到每日订单数量限制: {self.daily_order_count}/{max_daily_orders}")
            return False
        
        # 否则返回True
        return True

    def is_enough_balance(self, cost: float) -> bool:
        """
        检查账户余额是否足够支付给定的成本
        
        Args:
            cost: 预计成本
            
        Returns:
            如果余额足够则返回True，否则返回False
        """
        available_balance = self.get_account_balance()
        
        if available_balance <= 0:
            self.logger.error(f"账户可用资金为零或获取失败，无法进行交易")
            return False
            
        if available_balance < cost:
            self.logger.warning(f"账户可用资金不足: 需要 {cost}，但只有 {available_balance}")
            return False
            
        self.logger.info(f"账户资金充足: 需要 {cost}，可用 {available_balance}")
        return True

    async def risk_control_check(self, symbol: str, quantity: int, price: float, is_buy: bool) -> bool:
        """
        执行风险控制检查
        
        Args:
            symbol: 股票代码
            quantity: 数量
            price: 价格
            is_buy: 是否为买入操作
            
        Returns:
            bool: 如果通过风险控制检查则返回True，否则返回False
        """
        try:
            # 1. 检查每日订单上限
            if not self._check_daily_order_limit():
                return False
                
            # 2. 获取当前持仓
            positions = self.get_positions(symbol)
            
            if is_buy:
                # 3. 买入风险控制
                # 3.1 检查是否超过最大持仓数量
                current_position = 0
                for pos in positions:
                    if pos.symbol == symbol:
                        current_position += pos.quantity
                
                if current_position + quantity > self.max_position_size:
                    self.logger.warning(f"风险控制: 买入{symbol}的数量{quantity}会导致持仓({current_position})超过最大限制({self.max_position_size})")
                    return False
                
                # 3.2 检查账户余额是否足够
                cost = price * quantity * (1 + self.config.get('commission_rate', 0.0025))
                return self.is_enough_balance(cost)
            else:
                # 4. 卖出风险控制
                # 4.1 检查卖出数量是否超过当前持仓
                current_position = 0
                for pos in positions:
                    if pos.symbol == symbol:
                        current_position += pos.quantity
                
                if quantity > current_position:
                    self.logger.warning(f"风险控制: 卖出{symbol}的数量{quantity}超过当前持仓{current_position}")
                    return False
                    
                return True
        except Exception as e:
            self.logger.error(f"执行风险控制检查时发生错误: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    async def submit_buy_order(self, symbol: str, price: float, quantity: int, strategy_name: str = "default") -> OrderResult:
        """
        提交买入订单
        
        Args:
            symbol: 股票代码
            price: 价格
            quantity: 数量
            strategy_name: 策略名称
            
        Returns:
            OrderResult: 订单结果对象
        """
        self.logger.info(f"提交买入订单: {symbol}, 价格: {price}, 数量: {quantity}, 策略: {strategy_name}")
        
        # 执行风险控制检查
        if not await self.risk_control_check(symbol, quantity, price, is_buy=True):
            self.logger.warning(f"买入订单未通过风险控制检查: {symbol}, 价格: {price}, 数量: {quantity}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg="风险控制检查失败",
                strategy_name=strategy_name
            )
            
        # 提交订单
        return await self._submit_order(
            symbol=symbol,
            price=price,
            quantity=quantity,
            order_type="BUY",
            strategy_name=strategy_name
        )
        
    async def submit_sell_order(self, symbol: str, price: float, quantity: int, strategy_name: str = "default") -> OrderResult:
        """
        提交卖出订单
        
        Args:
            symbol: 股票代码
            price: 价格
            quantity: 数量
            strategy_name: 策略名称
            
        Returns:
            OrderResult: 订单结果对象
        """
        self.logger.info(f"提交卖出订单: {symbol}, 价格: {price}, 数量: {quantity}, 策略: {strategy_name}")
        
        # 执行风险控制检查
        if not await self.risk_control_check(symbol, quantity, price, is_buy=False):
            self.logger.warning(f"卖出订单未通过风险控制检查: {symbol}, 价格: {price}, 数量: {quantity}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Sell,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg="风险控制检查失败",
                strategy_name=strategy_name
            )
            
        # 提交订单
        return await self._submit_order(
            symbol=symbol,
            price=price,
            quantity=quantity,
            order_type="SELL",
            strategy_name=strategy_name
        )

    async def _init_trade_context(self):
        """初始化交易上下文
        
        Returns:
            bool: 如果初始化成功返回True，否则返回False
        """
        try:
            await self.initialize()
            self._trade_ctx_initialized = True
            return True
        except Exception as e:
            self.logger.error(f"初始化交易上下文失败: {e}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return False
