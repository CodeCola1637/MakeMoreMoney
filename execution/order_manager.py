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
import decimal

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
        
        # 添加最小价格变动单位和最小交易单位配置
        self.min_price_unit = {
            "700.HK": 0.2,  # 腾讯控股
            "9988.HK": 0.2,  # 阿里巴巴
            "AAPL.US": 0.01  # 苹果
        }
        self.min_quantity_unit = {
            "700.HK": 100,  # 腾讯控股
            "9988.HK": 100,  # 阿里巴巴
            "AAPL.US": 1  # 美股支持碎股交易，最小单位为1股
        }
        
        # 初始化不同市场的最小交易单位字典
        self.market_lot_sizes = {
            "HK": 100,  # 港股最小交易单位为100股
            "US": 1,    # 美股最小交易单位为1股
            "SH": 100,  # 上海A股最小交易单位为100股
            "SZ": 100   # 深圳A股最小交易单位为100股
        }
        
    async def initialize(self):
        """初始化订单管理器"""
        # 初始化状态
        self._trade_ctx_initialized = False
        self.active_orders = {}  # 活跃订单字典，key为订单ID
        self.order_update_time = {}  # 订单更新时间，用于订单超时判断
        
        # 获取订单跟踪配置
        self.order_check_interval = self.config.get("execution.order_tracking.check_interval", 60)
        self.order_timeout = self.config.get("execution.order_tracking.timeout", 300)
        self.retry_count = self.config.get("execution.order_tracking.retry_count", 3)
        
        # 初始化交易上下文
        await self._init_trade_context()
        
        # 启动订单状态跟踪任务
        self._start_order_tracking()
        
        return True

    def _start_order_tracking(self):
        """启动订单状态跟踪任务"""
        self.logger.info("启动订单状态跟踪任务...")
        self.order_tracking_task = asyncio.create_task(self._track_orders())
        
    async def _track_orders(self):
        """持续跟踪和更新所有活跃订单的状态"""
        self.logger.info(f"订单状态跟踪任务已启动，检查间隔: {self.order_check_interval}秒")
        
        while True:
            try:
                # 等待指定间隔
                await asyncio.sleep(self.order_check_interval)
                
                # 检查是否有活跃订单
                if not self.active_orders:
                    continue
                    
                self.logger.info(f"检查活跃订单状态，共{len(self.active_orders)}个订单")
                current_time = datetime.now()
                
                # 收集需要处理的订单
                orders_to_check = list(self.active_orders.items())
                
                for order_id, order in orders_to_check:
                    try:
                        # 获取最新订单状态
                        latest_order = await self._get_order_status(order_id)
                        
                        if not latest_order:
                            self.logger.warning(f"无法获取订单状态: {order_id}")
                            continue
                            
                        # 更新订单状态
                        if latest_order.status != order.status:
                            self.logger.info(f"订单状态更新: {order_id}, {order.status} -> {latest_order.status}")
                            order.status = latest_order.status
                            
                            # 如果订单已完成（成交、取消、拒绝），从活跃订单中移除
                            if order.is_filled() or order.is_canceled() or order.is_rejected():
                                self.logger.info(f"订单已完成: {order_id}, 状态: {order.status}")
                                del self.active_orders[order_id]
                                if order_id in self.order_update_time:
                                    del self.order_update_time[order_id]
                                    
                                # 通知回调
                                self._notify_order_update(order)
                            
                        # 检查订单是否超时
                        if (not order.is_filled() and not order.is_canceled() and not order.is_rejected() and
                                order_id in self.order_update_time):
                            elapsed = (current_time - self.order_update_time[order_id]).total_seconds()
                            
                            if elapsed > self.order_timeout:
                                self.logger.warning(f"订单超时: {order_id}, 已等待{elapsed:.1f}秒")
                                
                                # 尝试取消并重新提交
                                await self._handle_timeout_order(order)
                    except Exception as e:
                        self.logger.error(f"处理订单{order_id}状态时出错: {e}")
                
            except asyncio.CancelledError:
                self.logger.info("订单状态跟踪任务被取消")
                break
            except Exception as e:
                self.logger.error(f"订单状态跟踪任务出错: {e}")
                self.logger.error(f"Traceback:\n{traceback.format_exc()}")
                # 短暂暂停后继续
                await asyncio.sleep(10)

    async def _get_order_status(self, order_id: str):
        """获取订单最新状态"""
        try:
            # 检查交易上下文是否初始化
            if not self._trade_ctx_initialized:
                await self._init_trade_context()
                
            if not self._trade_ctx_initialized:
                self.logger.error("无法获取订单状态：交易上下文未初始化")
                return None
                
            # 获取订单详情
            order_info = self.trade_ctx.order_detail(order_id)
            if not order_info:
                self.logger.warning(f"无法获取订单详情: {order_id}")
                return None
                
            # 更新订单时间
            self.order_update_time[order_id] = datetime.now()
            
            return order_info
        except Exception as e:
            self.logger.error(f"获取订单状态失败: {order_id}, 错误: {e}")
            return None

    async def _handle_timeout_order(self, order):
        """处理超时订单，尝试取消并重新提交"""
        if not order or not order.order_id:
            return
            
        try:
            order_id = order.order_id
            
            # 尝试取消订单
            self.logger.info(f"尝试取消超时订单: {order_id}")
            cancel_result = await self._cancel_order(order_id)
            
            if cancel_result:
                self.logger.info(f"成功取消订单: {order_id}")
                
                # 等待短暂时间确保取消生效
                await asyncio.sleep(2)
                
                # 获取最新价格
                symbol = order.symbol
                quote = self.realtime_mgr.get_latest_quote(symbol) if hasattr(self, 'realtime_mgr') else None
                
                if quote:
                    latest_price = quote.last_done
                    self.logger.info(f"获取{symbol}最新价格: {latest_price}")
                    
                    # 创建新订单
                    side = "buy" if order.side == OrderSide.Buy else "sell"
                    await self._submit_order(
                        symbol=symbol,
                        price=latest_price,  # 使用最新价格
                        quantity=order.quantity,
                        order_type=side,
                        strategy_name=order.strategy_name
                    )
                else:
                    self.logger.warning(f"无法获取{symbol}最新价格，未重新提交订单")
            else:
                self.logger.warning(f"取消订单失败: {order_id}")
                
        except Exception as e:
            self.logger.error(f"处理超时订单时出错: {e}")
            self.logger.error(f"Traceback:\n{traceback.format_exc()}")

    async def _cancel_order(self, order_id: str) -> bool:
        """
        取消订单
        
        Args:
            order_id: 订单ID
            
        Returns:
            bool: 取消是否成功
        """
        try:
            # 检查交易上下文是否初始化
            if not self._trade_ctx_initialized:
                await self._init_trade_context()
                
            if not self._trade_ctx_initialized:
                self.logger.error("无法取消订单：交易上下文未初始化")
                return False
                
            # 取消订单
            self.trade_ctx.cancel_order(order_id)
            
            # 更新订单状态
            if order_id in self.active_orders:
                self.active_orders[order_id].status = OrderStatus.CancelSubmitted
                self._notify_order_update(self.active_orders[order_id])
                
            return True
        except Exception as e:
            self.logger.error(f"取消订单失败: {order_id}, 错误: {e}")
            return False

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
            elif signal.signal_type == SignalType.SELL:
                result = await self._create_sell_order(signal)
            else:  # HOLD 信号，不执行任何交易
                self.logger.info(f"收到HOLD信号，不执行交易: {symbol}")
                return None
            
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
        
        # 判断是否是美股，美股支持碎股交易
        is_us_stock = '.US' in symbol
        minimum_quantity = 1 if is_us_stock else self.get_lot_size(symbol)
        
        if available_cash <= 0:
            # 对于美股，如果账户余额不足但仍然要尝试交易，可以使用小数量尝试
            if is_us_stock:
                self.logger.warning(f"可用资金不足，尝试降低美股买入数量至 {minimum_quantity} 股: {symbol}")
                quantity = minimum_quantity
            else:
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
        price_float = float(price) if hasattr(price, '__float__') else price
        required_cash = price_float * quantity * 1.01  # 假设1%的手续费缓冲
        
        # 检查资金是否足够
        if required_cash > available_cash:
            self.logger.warning(f"可用资金不足，调整买入数量: {symbol}, 原始数量: {quantity}, 可用资金: {available_cash}, 所需资金: {required_cash}")
            
            # 计算可买数量
            affordable_quantity = int(available_cash / (price_float * 1.01))
            
            # 如果是美股且资金严重不足，设置一个最小交易数量
            if is_us_stock and affordable_quantity <= 0:
                affordable_quantity = minimum_quantity
                self.logger.warning(f"美股最小交易量设置为 {minimum_quantity} 股，尝试进行交易")
            elif affordable_quantity <= 0:
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
        提交订单
        
        Args:
            symbol: 股票代码
            price: 价格
            quantity: 数量
            order_type: 订单类型
            strategy_name: 策略名称
            
        Returns:
            OrderResult: 订单结果
        """
        try:
            # 自动调整价格到符合交易所规则
            adjusted_price = self._adjust_price_to_tick(symbol, price)
            
            # 记录详细的订单信息
            self.logger.info(f"准备提交订单 - 股票: {symbol}, 价格: {adjusted_price}, 数量: {quantity}, 类型: {order_type}, 策略: {strategy_name}")
            
            # 第二步：调整数量到符合手数要求
            adjusted_quantity = self._adjust_lot_size(symbol, quantity)
            self.logger.info(f"数量调整: {symbol} {quantity} -> {adjusted_quantity}")
            
            # 第三步：调用富途API
            order_resp = self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.LO,  # 修复：使用正确的OrderType.LO
                side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                submitted_quantity=quantity,
                time_in_force=TimeInForceType.Day,
                submitted_price=Decimal(str(adjusted_price)),
                remark=f"策略订单-{strategy_name}"
            )

            self.logger.info(f"订单提交成功: {symbol}, 订单ID: {order_resp.order_id}")
            
            # 增加今日订单计数
            self.daily_order_count += 1
            
            # 创建订单结果对象（移除id参数）
            result = OrderResult(
                order_id=order_resp.order_id,
                symbol=symbol,
                side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                quantity=quantity,
                price=adjusted_price,
                status=OrderStatus.NotReported,
                submitted_at=datetime.now(),
                msg="",
                strategy_name=strategy_name
            )
            
            # 保存到活跃订单
            self.active_orders[order_resp.order_id] = result
            self.order_update_time[order_resp.order_id] = time.time()
            
            # 保存订单到CSV
            self._save_order_to_csv(result)
            
            self.logger.info(f"订单提交完成: {result}")
            return result
            
        except Exception as e:
            self.logger.error(f"提交订单到交易所失败: {symbol}, 错误: {e}")
            
            # 创建失败的订单结果（移除id参数）
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                quantity=quantity,
                price=adjusted_price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=f"提交失败: {str(e)}",
                strategy_name=strategy_name
            )
    
    def _get_real_lot_size(self, symbol: str) -> int:
        """
        获取真实的港股手数信息（如果可用）
        
        Args:
            symbol: 股票代码
            
        Returns:
            真实手数，如果无法获取则返回默认值
        """
        # 已知的港股手数映射
        known_lot_sizes = {
            '700.HK': 100,      # 腾讯控股
            '9988.HK': 100,     # 阿里巴巴
            '388.HK': 100,      # 港交易所
            '1299.HK': 500,     # 友邦保险 - 500股为1手
            '941.HK': 500,      # 中国移动 - 500股为1手
        }
        
        if symbol in known_lot_sizes:
            self.logger.info(f"使用已知手数配置 {symbol}: {known_lot_sizes[symbol]}")
            return known_lot_sizes[symbol]
        
        # 如果没有已知配置，返回默认100股
        self.logger.warning(f"未找到 {symbol} 的确切手数信息，使用默认100股")
        return 100

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
                # 港股使用真实手数信息
                lot_size = self._get_real_lot_size(symbol)
                self.logger.debug(f"{symbol} 使用港股手数: {lot_size}")
            elif '.US' in symbol:
                # 美股最小手数为1（支持碎股交易）
                lot_size = 1
                self.logger.debug(f"{symbol} 美股支持碎股交易，最小手数: {lot_size}")
            elif '.SH' in symbol or '.SZ' in symbol:
                # A股默认最小手数为100
                lot_size = 100
                self.logger.debug(f"{symbol} 默认使用A股手数: {lot_size}")
            else:
                # 默认手数为100
                lot_size = 100
                self.logger.debug(f"{symbol} 未知市场，使用默认手数: {lot_size}")
                
            # 确保最小交易单位也更新到 min_quantity_unit 字典中
            if symbol not in self.min_quantity_unit:
                self.min_quantity_unit[symbol] = lot_size
                self.logger.info(f"更新 {symbol} 的最小交易单位为 {lot_size} 股")
                
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
            
            # 正确解析持仓数据结构：使用channels属性
            positions = []
            if hasattr(positions_response, 'channels') and positions_response.channels:
                for channel in positions_response.channels:
                    if hasattr(channel, 'positions') and channel.positions:
                        positions.extend(channel.positions)
            
            position_count = len(positions) if positions else 0
            self.logger.info(f"成功获取持仓, 共{position_count}个")
            
            # 记录详细持仓信息
            if positions:
                for pos in positions:
                    self.logger.debug(f"持仓: {pos.symbol}, 数量: {pos.quantity}, 可用: {pos.available_quantity}")
            
            # 如果指定了股票代码，则过滤持仓
            if symbol and positions:
                filtered_positions = [p for p in positions if p.symbol.upper() == symbol.upper()]
                self.logger.debug(f"过滤持仓 {symbol}, 结果: {len(filtered_positions)}个")
                return filtered_positions
                
            return positions
        except Exception as e:
            self.logger.error(f"获取持仓失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return []
            
    async def get_position(self, symbol: str):
        """
        获取指定股票的持仓
        
        Args:
            symbol: 股票代码
            
        Returns:
            如果持有则返回持仓对象，否则返回None
        """
        try:
            positions = self.get_positions(symbol)
            
            if positions and len(positions) > 0:
                return positions[0]  # 返回第一个匹配的持仓
            
            return None
        except Exception as e:
            self.logger.error(f"获取{symbol}持仓失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
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
        """关闭交易上下文并清理资源"""
        try:
            # 取消订单跟踪任务
            if hasattr(self, 'order_tracking_task') and self.order_tracking_task:
                self.order_tracking_task.cancel()
                try:
                    await self.order_tracking_task
                except asyncio.CancelledError:
                    pass
                    
            # 关闭交易上下文
            if self._trade_ctx_initialized and self.trade_ctx:
                self.logger.info("关闭交易上下文")
                # 检查是否有close方法，然后再调用
                if hasattr(self.trade_ctx, 'close'):
                    self.trade_ctx.close()
                else:
                    self.logger.warning("交易上下文没有close方法，忽略关闭操作")
                self._trade_ctx_initialized = False
                self.trade_ctx = None
        except Exception as e:
            self.logger.error(f"关闭交易上下文时出错: {e}")
            self.logger.error(traceback.format_exc())

    def _save_order(self, order_result: OrderResult, strategy_name: str):
        """
        保存订单信息
        
        Args:
            order_result: 订单结果
            strategy_name: 策略名称
        """
        try:
            # 确保日志目录存在
            log_dir = self.config.get('logging', {}).get('dir', 'logs')
            os.makedirs(log_dir, exist_ok=True)
            
            # 保存到CSV文件
            orders_file = os.path.join(log_dir, 'orders.csv')
            file_exists = os.path.exists(orders_file)
            
            with open(orders_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'order_id', 'symbol', 'side', 'quantity', 'price',
                    'status', 'submitted_at', 'filled_at', 'cancelled_at',
                    'rejected_at', 'msg', 'strategy_name'
                ])
                
                if not file_exists:
                    writer.writeheader()
                    
                writer.writerow({
                    'order_id': order_result.order_id,
                    'symbol': order_result.symbol,
                    'side': order_result.side.name if hasattr(order_result.side, 'name') else str(order_result.side),
                    'quantity': order_result.quantity,
                    'price': order_result.price,
                    'status': order_result.status.name if hasattr(order_result.status, 'name') else str(order_result.status),
                    'submitted_at': order_result.submitted_at.isoformat() if order_result.submitted_at else '',
                    'filled_at': order_result.filled_at.isoformat() if hasattr(order_result, 'filled_at') and order_result.filled_at else '',
                    'cancelled_at': order_result.cancelled_at.isoformat() if hasattr(order_result, 'cancelled_at') and order_result.cancelled_at else '',
                    'rejected_at': order_result.rejected_at.isoformat() if hasattr(order_result, 'rejected_at') and order_result.rejected_at else '',
                    'msg': order_result.msg if hasattr(order_result, 'msg') else '',
                    'strategy_name': strategy_name
                })
                
            self.logger.info(f"订单信息已保存到 {orders_file}")
            
        except Exception as e:
            self.logger.error(f"保存订单信息时发生错误: {str(e)}")
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

    def is_enough_balance(self, cost: float, symbol: str = None) -> bool:
        """检查账户余额是否足够支付交易成本
        
        Args:
            cost: 交易成本
            symbol: 股票代码，用于判断使用哪种货币
            
        Returns:
            是否有足够余额
        """
        try:
            # 获取账户余额
            balance_response = self.trade_ctx.account_balance()
            
            # 根据股票类型确定使用的货币
            is_us_stock = symbol and '.US' in symbol
            is_hk_stock = symbol and '.HK' in symbol
            
            if isinstance(balance_response, list):
                # 查找对应货币账户
                target_currency = "USD" if is_us_stock else "HKD"
                available_balance = 0.0
                
                for item in balance_response:
                    if hasattr(item, 'cash_infos') and item.cash_infos:
                        for cash_info in item.cash_infos:
                            if hasattr(cash_info, 'currency') and cash_info.currency == target_currency and hasattr(cash_info, 'available_cash'):
                                available_balance = float(cash_info.available_cash)
                                self.logger.info(f"检查{target_currency}账户余额: {available_balance}")
                                break
                
                # 检查对应货币账户余额
                if available_balance >= cost:
                    self.logger.info(f"{target_currency}账户余额充足: {available_balance} >= {cost}")
                    return True
                else:
                    self.logger.warning(f"{target_currency}账户余额不足: {available_balance} < {cost}")
            return False
            
            # 如果无法获取具体货币余额，使用总可用资金（兼容性处理）
            total_available = self.get_account_balance()
            
            # 检查是否足够
            if total_available >= cost:
                self.logger.info(f"总账户余额充足: {total_available} >= {cost}")
                return True
            else:
                self.logger.warning(f"总账户余额不足: {total_available} < {cost}")
                return False
                
        except Exception as e:
            self.logger.error(f"检查账户余额时出错: {str(e)}")
            return False

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
            self.logger.info(f"开始风险控制检查: {symbol}, 数量={quantity}, 价格={price}, 买入={is_buy}")
            
            # 1. 检查每日订单上限
            if not self._check_daily_order_limit():
                self.logger.warning(f"风险控制: 今日订单数已达上限 {self.daily_order_count}/{self.max_daily_orders}")
                return False
                
            # 2. 获取当前持仓
            positions = self.get_positions(symbol)
            self.logger.info(f"当前持仓: {positions}")
            
            # 判断是否是美股交易
            is_us_stock = '.US' in symbol
            
            if is_buy:
                # 3. 买入风险控制
                # 3.1 检查是否超过最大持仓数量
                current_position = 0
                for pos in positions:
                    if pos.symbol == symbol:
                        current_position += float(pos.quantity)
                
                self.logger.info(f"当前持仓数量: {current_position}, 最大持仓限制: {self.max_position_size}")
                if current_position + float(quantity) > self.max_position_size:
                    self.logger.warning(f"风险控制: 买入{symbol}的数量{quantity}会导致持仓({current_position})超过最大限制({self.max_position_size})")
                    return False
                
                # 3.2 检查账户余额是否足够
                # 确保所有类型转换为float
                price_float = float(price)
                quantity_float = float(quantity)
                commission_rate = float(self.config.get('commission_rate', 0.0025))
                
                cost = price_float * quantity_float * (1 + commission_rate)
                self.logger.info(f"计算交易成本: {cost}")
                
                # 首先进行基本的资金检查
                balance_check = self.is_enough_balance(cost, symbol)
                
                # 如果资金充足，正常通过
                if balance_check:
                    return True
                
                # 资金不足的情况下，检查账户总余额是否为负
                total_balance = self.get_account_balance()
                if total_balance < 0:
                    self.logger.warning(f"风险控制: 账户总余额为负({total_balance})，禁止任何买入交易: {symbol}")
                    return False
                
                # 美股小额交易特殊处理（仅在总余额为正时）
                if is_us_stock and quantity <= 5 and total_balance > cost:
                    self.logger.info(f"美股小额交易({quantity}股)，在总余额为正的情况下放宽部分限制")
                    return True
                
                # 其他情况拒绝交易
                self.logger.warning(f"风险控制: 账户余额不足，需要{cost}，当前可用资金: {total_balance}")
                return False
            else:
                # 4. 卖出风险控制
                # 4.1 检查卖出数量是否超过当前持仓
                current_position = 0
                for pos in positions:
                    if pos.symbol == symbol:
                        current_position += float(pos.quantity)
                
                self.logger.info(f"当前持仓数量: {current_position}, 计划卖出数量: {quantity}")
                if float(quantity) > current_position:
                    self.logger.warning(f"风险控制: 卖出{symbol}的数量{quantity}超过当前持仓{current_position}")
                    return False
                    
                return True
        except Exception as e:
            self.logger.error(f"执行风险控制检查时发生错误: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def _get_hk_price_tick(self, price) -> float:
        """
        获取港股价格的最小变动单位（价格精度）
        
        Args:
            price: 股票价格 (支持 float 或 Decimal)
            
        Returns:
            最小价格变动单位
        """
        try:
            # 统一转换为 float 进行计算
            price_float = float(price) if price else 0.0
            
            if price_float <= 0.25:
                return 0.001
            elif price_float <= 0.50:
                return 0.005
            elif price_float <= 10.00:
                return 0.01
            elif price_float <= 20.00:
                return 0.02
            elif price_float <= 100.00:
                return 0.05
            elif price_float <= 200.00:
                return 0.10
            elif price_float <= 500.00:
                return 0.20
            else:
                return 0.50
        except (ValueError, TypeError) as e:
            self.logger.error(f"获取港股价格精度时出错: {e}, 使用默认精度0.05")
            return 0.05
    
    def _adjust_price_to_tick(self, symbol: str, price) -> float:
        """
        调整价格到符合最小变动单位的价格
        
        Args:
            symbol: 股票代码
            price: 原始价格 (支持 float 或 Decimal)
            
        Returns:
            调整后的价格
        """
        try:
            # 统一转换为 float，支持 Decimal 和 float 类型
            if isinstance(price, Decimal):
                price_float = float(price)
            elif isinstance(price, (int, float)):
                price_float = float(price)
            else:
                # 尝试转换字符串或其他类型
                price_float = float(str(price))
            
            if ".HK" in symbol:
                tick = self._get_hk_price_tick(price_float)
                # 调整到最接近的有效价格点
                adjusted_price = round(price_float / tick) * tick
                if abs(adjusted_price - price_float) > 0.001:
                    self.logger.info(f"调整港股价格 {symbol}: {price_float} -> {adjusted_price} (精度: {tick})")
                return round(adjusted_price, 3)  # 保留3位小数
            elif ".US" in symbol:
                # 美股精度为0.01
                adjusted_price = round(price_float, 2)
                return adjusted_price
            else:
                # 其他市场默认精度为0.01
                adjusted_price = round(price_float, 2)
                return adjusted_price
        except (ValueError, TypeError, decimal.InvalidOperation) as e:
            self.logger.error(f"调整价格精度时出错: {e}, 使用原始价格: {price}")
            try:
                return float(price) if price else 0.0
            except:
                return 0.0

    def _validate_order_parameters(self, symbol: str, price, quantity: int) -> Tuple[bool, str]:
        """
        验证订单参数是否符合交易所规则
        
        Args:
            symbol: 股票代码
            price: 价格 (支持 float 或 Decimal)
            quantity: 数量
            
        Returns:
            (是否有效, 错误信息)
        """
        try:
            # 统一转换价格类型
            if isinstance(price, Decimal):
                price_float = float(price)
            elif isinstance(price, (int, float)):
                price_float = float(price)
            else:
                price_float = float(str(price))
            
            # 检查基本参数
            if price_float <= 0:
                return False, "价格必须大于0"
            
            if quantity <= 0:
                return False, "数量必须大于0"
            
            # 港股特殊验证
            if ".HK" in symbol:
                # 获取港股价格精度
                tick = self._get_hk_price_tick(price_float)
                
                # 检查价格精度
                remainder = price_float % tick
                if remainder > 0.0001:  # 考虑浮点数精度误差
                    # 尝试调整价格
                    adjusted_price = round(price_float / tick) * tick
                    adjusted_price = round(adjusted_price, 3)
                    self.logger.warning(f"价格{price_float}不符合最小价格变动单位{tick}，建议使用{adjusted_price}")
                    return False, f"价格{price_float}不符合最小价格变动单位{tick}，建议使用{adjusted_price}"
                
                # 检查手数
                lot_size = self.get_lot_size(symbol)
                if quantity % lot_size != 0:
                    return False, f"数量{quantity}不符合最小交易手数{lot_size}的倍数"
            
            # 美股验证
            elif ".US" in symbol:
                # 美股价格精度为0.01
                if round(price_float, 2) != price_float:
                    adjusted_price = round(price_float, 2)
                    return False, f"价格{price_float}精度过高，建议使用{adjusted_price}"
            
            return True, ""
            
        except Exception as e:
            error_msg = f"验证订单参数时发生错误: {e}"
            self.logger.error(error_msg)
            return False, error_msg

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
        
        # 验证订单参数
        is_valid, error_msg = self._validate_order_parameters(symbol, price, quantity)
        if not is_valid:
            self.logger.error(f"订单参数验证失败: {error_msg}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=error_msg,
                strategy_name=strategy_name
            )
        
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
                msg="未通过风险控制检查",
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
        
        # 验证订单参数
        is_valid, error_msg = self._validate_order_parameters(symbol, price, quantity)
        if not is_valid:
            self.logger.error(f"订单参数验证失败: {error_msg}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Sell,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=error_msg,
                strategy_name=strategy_name
            )
        
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
                msg="未通过风险控制检查",
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
        """初始化交易上下文，带重试机制"""
        # 如果已经初始化，则直接返回
        if self._trade_ctx_initialized:
            return True
            
        self.logger.info("正在初始化交易上下文...")
        
        # 重试逻辑
        max_retries = self.config.get("execution.order_tracking.retry_count", 3)
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
                    
                    # 保存获取到的账户余额
                    self.account_balance = {}
                    if hasattr(balance, 'cash'):
                        # 按货币类型保存余额
                        for cash_info in balance.cash:
                            self.account_balance[cash_info.currency] = cash_info.available
                    
                    # 计算总可用资金（用于风控）
                    self.total_available_cash = self.get_account_balance()
                    
                except Exception as e:
                    self.logger.warning(f"获取账户余额失败，但上下文已创建: {e}")
                
                self._trade_ctx_initialized = True
                self.logger.info("交易上下文初始化完成")
                return True
            except Exception as e:
                self.logger.error(f"交易上下文初始化失败 (尝试 {attempt}/{max_retries}): {e}")
                # 使用指数退避策略
                if attempt < max_retries:
                    wait_time = retry_delay * (2 ** (attempt - 1))
                    self.logger.info(f"等待 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.error("已达到最大重试次数，无法初始化交易上下文")
                    self._trade_ctx_initialized = False
                    return False
        
        return False

    def _notify_order_update(self, order: OrderResult):
        """通知订单状态更新"""
        try:
            if hasattr(self, 'order_callbacks') and self.order_callbacks:
                for callback in self.order_callbacks:
                    try:
                        callback(order)
                    except Exception as e:
                        self.logger.error(f"执行订单回调时出错: {e}")
            else:
                # 如果没有回调，仍然记录订单状态变化
                self.logger.info(f"订单状态变化: {order.order_id}, {order.symbol}, {order.status}")
        except Exception as e:
            self.logger.error(f"通知订单更新时出错: {e}")
            self.logger.error(f"Traceback:\n{traceback.format_exc()}")

    async def on_signal(self, signal: Signal):
        """
        处理交易信号
        
        Args:
            signal: 交易信号对象
        """
        try:
            self.logger.info(f"收到交易信号: {signal}")
            
            # 检查信号有效性
            if not self._validate_signal(signal):
                self.logger.warning(f"信号验证失败，跳过执行")
                return
                
            # 执行风控检查
            if not await self.risk_control_check(signal):
                self.logger.warning(f"风控检查未通过，跳过执行")
                return
                
            # 确定订单类型和价格
            order_type = OrderType[self.config.get("execution.order_types.default", "LO")]
            price = signal.price
            
            # 根据信号类型调整价格
            if signal.signal_type == SignalType.BUY:
                price_adjust_rate = self.config.get("execution.price_adjust_rate.buy", 0.003)
                price *= (1 + price_adjust_rate)
                side = OrderSide.Buy
            else:  # SELL
                price_adjust_rate = self.config.get("execution.price_adjust_rate.sell", 0.003)
                price *= (1 - price_adjust_rate)
                side = OrderSide.Sell
                
            # 提交订单
            order_result = await self.submit_order(
                symbol=signal.symbol,
                side=side,
                quantity=signal.quantity,
                price=price,
                order_type=order_type,
                strategy_name=signal.strategy_name
            )
            
            if order_result:
                self.logger.info(f"订单提交成功: {order_result.order_id}")
            else:
                self.logger.error("订单提交失败")
                
        except Exception as e:
            self.logger.error(f"处理交易信号时出错: {e}")
            import traceback
            traceback.print_exc()
            
    def _validate_signal(self, signal: Signal) -> bool:
        """
        验证交易信号
        
        Args:
            signal: 交易信号对象
            
        Returns:
            bool: 信号是否有效
        """
        try:
            # 检查必要字段
            if not all([signal.symbol, signal.signal_type, signal.price, signal.quantity]):
                self.logger.warning(f"信号缺少必要字段: {signal}")
                return False
                
            # 检查价格和数量是否为正数
            if signal.price <= 0 or signal.quantity <= 0:
                self.logger.warning(f"价格或数量必须为正数: price={signal.price}, quantity={signal.quantity}")
                return False
                
            # 检查信号类型
            if signal.signal_type not in [SignalType.BUY, SignalType.SELL]:
                self.logger.warning(f"不支持的信号类型: {signal.signal_type}")
                return False
                
            # 检查信号时效性
            signal_age = datetime.now() - signal.created_at
            if signal_age > timedelta(minutes=5):  # 信号有效期5分钟
                self.logger.warning(f"信号已过期: {signal_age.total_seconds()}秒")
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"验证信号时出错: {e}")
            return False

    async def place_order(self, symbol: str, side: str, quantity: int, price_type: str = "LIMIT", price: float = None):
        """
        下单
        
        Args:
            symbol: 股票代码
            side: 交易方向，"BUY"或"SELL"
            quantity: 数量
            price_type: 价格类型，"LIMIT"或"MARKET"
            price: 价格，仅限价单需要
            
        Returns:
            订单结果
        """
        try:
            self.logger.info(f"下单: {symbol}, 方向: {side}, 数量: {quantity}, 类型: {price_type}")
            
            # 转换交易方向
            order_side = OrderSide.Buy if side.upper() == "BUY" else OrderSide.Sell
            
            # 对于美股，如果余额不足且购买数量大于10，尝试减少到10股
            is_us_stock = '.US' in symbol
            available_balance = self.get_account_balance()
            
            if is_us_stock and order_side == OrderSide.Buy and available_balance <= 0:
                if quantity > 10:  # 如果原始数量大于10股
                    self.logger.info(f"美股交易: 账户余额不足，尝试减少交易数量至10股进行尝试")
                    quantity = 10
            
            # 如果是市价单
            if price_type.upper() == "MARKET":
                # 确保价格有效
                if price is None or price <= 0:
                    price = 100  # 默认价格
                    self.logger.warning(f"市价单需要有效价格，使用默认价格: {price}")
                
                # 调用提交市价单方法
                if order_side == OrderSide.Buy:
                    return await self.submit_buy_order(symbol, price, quantity, "default")
                else:
                    return await self.submit_sell_order(symbol, price, quantity, "default")
            else:
                # 限价单必须有价格
                if price is None or price <= 0:
                    self.logger.error("限价单必须指定有效价格")
                    return OrderResult(
                        order_id="",
                        symbol=symbol,
                        side=order_side,
                        quantity=quantity,
                        price=0,
                        status=OrderStatus.Rejected,
                        submitted_at=datetime.now(),
                        msg="限价单必须指定有效价格",
                        strategy_name="default"
                    )
                
                # 调用提交限价单方法
                if order_side == OrderSide.Buy:
                    return await self.submit_buy_order(symbol, price, quantity, "default")
                else:
                    return await self.submit_sell_order(symbol, price, quantity, "default")
                    
        except Exception as e:
            self.logger.error(f"下单失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return None

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
            
            # 美股特殊处理 - 支持碎股交易
            if '.US' in symbol:
                # 美股最小交易单位为1股，不需要调整为整手
                if quantity < 1:
                    adjusted_quantity = 1
                    self.logger.info(f"美股交易数量小于1股，调整为最小交易单位: 1股")
                else:
                    adjusted_quantity = quantity
                return adjusted_quantity
            
            # 非美股市场，调整为整手数量
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

    def _get_real_lot_size(self, symbol: str) -> int:
        """
        获取真实的港股手数信息（如果可用）
        
        Args:
            symbol: 股票代码
            
        Returns:
            真实手数，如果无法获取则返回默认值
        """
        # 已知的港股手数映射
        known_lot_sizes = {
            '700.HK': 100,      # 腾讯控股
            '9988.HK': 100,     # 阿里巴巴
            '388.HK': 100,      # 港交易所
            '1299.HK': 500,     # 友邦保险 - 500股为1手
            '941.HK': 500,      # 中国移动 - 500股为1手
        }
        
        if symbol in known_lot_sizes:
            self.logger.info(f"使用已知手数配置 {symbol}: {known_lot_sizes[symbol]}")
            return known_lot_sizes[symbol]
        
        # 如果没有已知配置，返回默认100股
        self.logger.warning(f"未找到 {symbol} 的确切手数信息，使用默认100股")
        return 100

    async def _risk_control_check(self, symbol: str, quantity: int, price: float, is_buy: bool) -> bool:
        """
        执行风险控制检查（内部方法）
        
        Args:
            symbol: 股票代码
            quantity: 数量
            price: 价格
            is_buy: 是否为买入操作
            
        Returns:
            bool: 如果通过风险控制检查则返回True，否则返回False
        """
        try:
            self.logger.info(f"开始风险控制检查: {symbol}, 数量={quantity}, 价格={price}, 买入={is_buy}")
            
            # 1. 检查每日订单上限
            if not self._check_daily_order_limit():
                self.logger.warning(f"风险控制: 今日订单数已达上限 {self.daily_order_count}/{self.max_daily_orders}")
                return False
                
            # 2. 获取当前持仓
            positions = self.get_positions(symbol)
            self.logger.info(f"当前持仓: {positions}")
            
            # 判断是否是美股交易
            is_us_stock = '.US' in symbol
            
            if is_buy:
                # 3. 买入风险控制
                # 3.1 检查是否超过最大持仓数量
                current_position = 0
                for pos in positions:
                    if pos.symbol == symbol:
                        current_position += float(pos.quantity)
                
                self.logger.info(f"当前持仓数量: {current_position}, 最大持仓限制: {self.max_position_size}")
                if current_position + float(quantity) > self.max_position_size:
                    self.logger.warning(f"风险控制: 买入{symbol}的数量{quantity}会导致持仓({current_position})超过最大限制({self.max_position_size})")
                    return False
                
                # 3.2 检查账户余额是否足够
                try:
                    # 确保所有类型转换为float
                    price_float = float(price)
                    quantity_float = float(quantity)
                    commission_rate = float(self.config.get('commission_rate', 0.0025))
                    
                    cost = price_float * quantity_float * (1 + commission_rate)
                    self.logger.info(f"计算交易成本: {cost}")
                    
                    # 获取账户余额
                    balance_response = self.trade_ctx.account_balance()
                    
                    # 根据股票类型确定使用的货币
                    target_currency = "USD" if is_us_stock else "HKD"
                    available_balance = 0.0
                    
                    if isinstance(balance_response, list):
                        self.logger.debug(f"账户余额对象类型: {type(balance_response)}")
                        self.logger.info(f"账户余额是列表格式，包含 {len(balance_response)} 项")
                        
                        # 获取USD和HKD可用资金
                        usd_balance = 0.0
                        hkd_balance = 0.0
                        
                        for item in balance_response:
                            if hasattr(item, 'cash_infos') and item.cash_infos:
                                for cash_info in item.cash_infos:
                                    if hasattr(cash_info, 'currency') and hasattr(cash_info, 'available_cash'):
                                        if cash_info.currency == "USD":
                                            usd_balance = float(cash_info.available_cash)
                                            self.logger.info(f"获取到USD可用资金: {usd_balance}")
                                        elif cash_info.currency == "HKD":
                                            hkd_balance = float(cash_info.available_cash)
                                            self.logger.info(f"获取到HKD可用资金: {hkd_balance}")
                        
                        # 检查目标货币余额
                        if target_currency == "USD":
                            available_balance = usd_balance
                        else:
                            available_balance = hkd_balance
                        
                        # 检查对应货币账户余额
                        if available_balance >= cost:
                            self.logger.info(f"{target_currency}账户余额充足: {available_balance} >= {cost}")
                            return True
                        else:
                            self.logger.warning(f"{target_currency}账户余额不足: {available_balance} < {cost}")
                            
                            # 计算总可用资金（按汇率转换）
                            # 假设汇率为 1 USD = 7.8 HKD
                            exchange_rate = 7.8
                            if target_currency == "USD":
                                # 需要USD，检查能否用HKD兑换
                                total_available = usd_balance + (hkd_balance / exchange_rate)
                            else:
                                # 需要HKD，检查能否用USD兑换
                                total_available = hkd_balance + (usd_balance * exchange_rate)
                            
                            self.logger.info(f"账户总可用资金: {total_available}")
                            
                            if total_available >= cost:
                                self.logger.info(f"总可用资金充足，允许跨币种交易")
                                return True
                            else:
                                self.logger.warning(f"风险控制: 账户余额不足，需要{cost}，当前可用资金: {total_available}")
                                return False
                    else:
                        # 兼容处理：如果不是列表格式，直接获取总余额
                        total_balance = self.get_account_balance()
                        if total_balance >= cost:
                            self.logger.info(f"总余额充足: {total_balance} >= {cost}")
                            return True
                        else:
                            self.logger.warning(f"风险控制: 账户余额不足，需要{cost}，当前总余额: {total_balance}")
                            return False
                            
                except Exception as e:
                    self.logger.error(f"风险控制检查中发生错误: {e}")
                    return False
            else:
                # 4. 卖出风险控制
                # 4.1 检查卖出数量是否超过当前持仓
                current_position = 0
                for pos in positions:
                    if pos.symbol == symbol:
                        current_position += float(pos.quantity)
                
                self.logger.info(f"当前持仓数量: {current_position}, 计划卖出数量: {quantity}")
                if float(quantity) > current_position:
                    self.logger.warning(f"风险控制: 卖出{symbol}的数量{quantity}超过当前持仓{current_position}")
                    return False
                    
                return True
        except Exception as e:
            self.logger.error(f"执行风险控制检查时发生错误: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def _save_order_to_csv(self, result: OrderResult):
        """
        保存订单信息到CSV文件
        
        Args:
            result: 订单结果对象
        """
        try:
            # 确保logs目录存在
            os.makedirs("logs", exist_ok=True)
            
            # CSV文件路径
            csv_file = "logs/orders.csv"
            
            # 检查文件是否存在，不存在则创建并写入标题行
            file_exists = os.path.exists(csv_file)
            
            with open(csv_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # 如果文件不存在，写入标题行
                if not file_exists:
                    writer.writerow([
                        'timestamp', 'order_id', 'symbol', 'side', 'quantity', 
                        'price', 'status', 'executed_quantity'
                    ])
                
                # 写入订单数据
                writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    result.id,
                    result.symbol,
                    result.side.value,
                    result.quantity,
                    result.price,
                    result.status.value,
                    result.executed_quantity
                ])
                
            self.logger.info(f"订单信息已保存到 {csv_file}")
            
        except Exception as e:
            self.logger.error(f"保存订单信息到CSV时出错: {e}")
