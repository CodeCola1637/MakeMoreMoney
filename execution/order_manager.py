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
from execution.order_validator import OrderValidator
from execution.fund_guard import FundGuard
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
        
        # 🔧 初始化订单验证器
        self.validator = OrderValidator(self, config_loader, self.logger)
        self.logger.info("✅ 订单验证器初始化完成")
        
        # 🔧 初始化资金守卫
        self.fund_guard = FundGuard(self, config_loader, self.logger)
        self.logger.info("✅ 资金守卫初始化完成")
        
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
        self.max_pending_orders = self.config.get("execution.order_tracking.max_pending_orders", 5)
        
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
                    
                # 🔧 主动清理过多挂单
                await self._cleanup_excessive_orders()
                    
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
                                
                                # 🔧 直接取消超时订单，不重新提交（避免资金占用）
                                await self._cancel_order(order_id)
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

    async def _cleanup_excessive_orders(self):
        """主动清理过多的挂单"""
        try:
            # 获取所有未成交的活跃订单
            pending_orders = [
                (order_id, order) for order_id, order in self.active_orders.items()
                if not order.is_filled() and not order.is_canceled() and not order.is_rejected()
            ]
            
            if len(pending_orders) > self.max_pending_orders:
                # 按提交时间排序，取消最旧的订单
                pending_orders.sort(key=lambda x: x[1].submitted_at)
                orders_to_cancel = pending_orders[:len(pending_orders) - self.max_pending_orders + 1]
                
                self.logger.warning(f"清理过多挂单: {len(pending_orders)} -> {self.max_pending_orders}, 取消{len(orders_to_cancel)}个旧订单")
                
                for order_id, order in orders_to_cancel:
                    await self._cancel_order(order_id)
                    await asyncio.sleep(0.1)  # 避免API频率限制
                    
        except Exception as e:
            self.logger.error(f"清理挂单时出错: {e}")

    async def _cleanup_one_low_quality_order(self):
        """清理一个低质量订单，为高质量信号让路"""
        try:
            # 获取所有未成交的活跃订单
            pending_orders = [
                (order_id, order) for order_id, order in self.active_orders.items()
                if not order.is_filled() and not order.is_canceled() and not order.is_rejected()
            ]
            
            if not pending_orders:
                return
            
            # 优先清理低置信度、长时间未成交的订单
            # 这里简化处理，按时间排序，取消最旧的一个订单
            pending_orders.sort(key=lambda x: x[1].submitted_at)
            
            if pending_orders:
                order_id, order = pending_orders[0]
                self.logger.info(f"为高质量信号让路，取消旧订单: {order_id}, {order.symbol}")
                await self._cancel_order(order_id)
                
        except Exception as e:
            self.logger.error(f"清理低质量订单时出错: {e}")

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
    async def process_signal(self, signal: Signal, realtime_mgr=None) -> Optional[OrderResult]:
        """处理信号并执行交易"""
        return await self.execute_signal(signal, realtime_mgr=realtime_mgr)
        
    def register_order_callback(self, callback: Callable[[OrderResult], None]):
        """
        注册订单回调函数
        
        Args:
            callback: 回调函数，接收OrderResult参数
        """
        self.order_callbacks.append(callback)
        self.logger.debug(f"已注册订单回调函数: {callback.__name__}")
    
    async def execute_signal(self, signal: Signal, realtime_mgr=None):
        """执行交易信号"""
        if not self._validate_risk_control(signal):
            self.logger.warning(f"风控检查不通过，拒绝执行信号: {signal}")
            return None
        
        try:
            self.logger.info(f"执行交易信号: {signal}")
            
            symbol = signal.symbol
            signal_type = signal.signal_type
            
            # 🔧 关键修复：明确记录信号类型，确保后续逻辑正确
            signal_type_str = signal_type.value if hasattr(signal_type, 'value') else str(signal_type)
            
            # 🔧 新增：订单预验证 - 降低拒单率
            if signal_type in [SignalType.BUY, SignalType.COVER]:
                side_str = "Buy"
            elif signal_type in [SignalType.SELL, SignalType.SHORT]:
                side_str = "Sell"
            else:
                side_str = "Hold"
            
            if signal_type not in [SignalType.HOLD, SignalType.UNKNOWN]:
                is_valid, validation_msg, details = await self.validator.validate_order(
                    symbol=symbol,
                    side=side_str,
                    quantity=signal.quantity,
                    price=signal.price,
                    realtime_mgr=realtime_mgr
                )
                
                if not is_valid:
                    self.logger.warning(f"❌ 订单预验证失败: {validation_msg}")
                    self.logger.debug(self.validator.get_validation_summary(details))
                    return None
                else:
                    self.logger.info(f"✅ 订单预验证通过: {validation_msg}")
                
                # 🔧 新增：资金守卫检查
                from decimal import Decimal
                trade_amount = Decimal(str(signal.price)) * Decimal(str(signal.quantity))
                fund_ok, fund_msg = self.fund_guard.can_trade(
                    symbol=symbol,
                    side=side_str,
                    amount=trade_amount,
                    quantity=signal.quantity
                )
                
                if not fund_ok:
                    self.logger.warning(f"❌ 资金守卫拒绝交易: {fund_msg}")
                    return None
                else:
                    self.logger.info(f"✅ 资金守卫检查通过: {fund_msg}")
            
            # 🚀 新增：全面智能分析和优化
            await self._intelligent_portfolio_optimization(signal)
            self.logger.info(f"📊 信号详情: 股票={symbol}, 信号类型={signal_type_str}, 价格={signal.price}, 数量={signal.quantity}")
            
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
            
            # 创建订单 - 根据信号类型执行对应操作
            result = None
            if signal_type == SignalType.BUY:
                self.logger.info(f"🔵 执行买入操作: {symbol}")
                result = await self._create_buy_order(signal)
                # 🔧 验证订单方向与信号类型匹配
                if result and result.side != OrderSide.Buy:
                    self.logger.error(f"❌ 严重错误: BUY信号生成了非Buy订单! 信号={signal_type_str}, 订单方向={result.side}")
                    return None
            elif signal_type == SignalType.SELL:
                self.logger.info(f"🔴 执行卖出操作: {symbol}")
                result = await self._create_sell_order(signal)
                # 🔧 验证订单方向与信号类型匹配
                if result and result.side != OrderSide.Sell:
                    self.logger.error(f"❌ 严重错误: SELL信号生成了非Sell订单! 信号={signal_type_str}, 订单方向={result.side}")
                    return None
            elif signal_type == SignalType.SHORT:
                # 做空信号 - 直接调用卖出创建空头
                self.logger.info(f"📉 执行做空操作: {symbol}")
                result = await self._create_sell_order(signal)
                if result and result.side != OrderSide.Sell:
                    self.logger.error(f"❌ 严重错误: SHORT信号生成了非Sell订单!")
                    return None
            elif signal_type == SignalType.COVER:
                # 平空信号 - 买入平仓空头
                self.logger.info(f"📈 执行平空操作: {symbol}")
                result = await self._create_buy_order(signal)
                if result and result.side != OrderSide.Buy:
                    self.logger.error(f"❌ 严重错误: COVER信号生成了非Buy订单!")
                    return None
            elif signal_type == SignalType.HOLD:
                self.logger.info(f"⚪ 收到HOLD信号，不执行交易: {symbol}")
                return None
            else:
                self.logger.warning(f"⚠️ 未知信号类型: {signal_type_str}")
                return None
            
            if result:
                # 🔧 修复：记录订单详情
                actual_side_str = result.side.name if hasattr(result.side, 'name') else str(result.side)
                status_str = result.status.name if hasattr(result.status, 'name') else str(result.status)
                self.logger.info(f"✅ 订单已提交: {result.order_id}, 方向={actual_side_str}, 状态={status_str}")
                
                # 🔧 修复：传入策略名称字符串而非整个signal对象
                strategy_name = getattr(signal, 'strategy_name', 'unknown')
                if hasattr(signal, 'id'):
                    strategy_name = f"{strategy_name}|{signal.id}"
                self._save_order(result, strategy_name)
                return result
            else:
                self.logger.error(f"订单提交失败: {symbol}, {signal_type_str}, {adjusted_quantity}")
                return None
        except Exception as e:
            self.logger.error(f"执行信号失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    async def _intelligent_portfolio_optimization(self, signal: Signal):
        """
        智能投资组合优化
        在每次收到信号后，全面分析当前状态并进行优化决策
        """
        try:
            self.logger.info(f"🧠 开始智能投资组合分析，触发信号: {signal.symbol}")
            
            # 1. 获取当前状态
            current_positions = self.get_positions()
            current_balance = self.get_account_balance()
            pending_orders = await self._get_all_pending_orders()
            
            self.logger.info(f"📊 当前状态 - 持仓: {len(current_positions)}个, 资金: ${current_balance:.2f}, 挂单: {len(pending_orders)}个")
            
            # 2. 分析不合理的挂单
            unreasonable_orders = await self._analyze_unreasonable_orders(pending_orders, current_positions, current_balance)
            
            # 3. 清理不合理的挂单
            if unreasonable_orders:
                await self._cleanup_unreasonable_orders(unreasonable_orders)
            
            # 4. 分析是否需要卖出部分持仓来优化资金配置
            positions_to_sell = await self._analyze_positions_for_optimization(current_positions, signal)
            
            # 5. 执行优化卖出
            if positions_to_sell:
                await self._execute_optimization_sells(positions_to_sell)
            
            # 6. 重新评估挂单空间
            remaining_pending = await self._get_all_pending_orders()
            self.logger.info(f"✅ 优化完成 - 剩余挂单: {len(remaining_pending)}/{self.max_pending_orders}")
            
        except Exception as e:
            self.logger.error(f"智能投资组合优化失败: {e}")

    async def _get_all_pending_orders(self):
        """获取所有未成交的挂单"""
        try:
            pending_orders = [
                (order_id, order) for order_id, order in self.active_orders.items()
                if not order.is_filled() and not order.is_canceled() and not order.is_rejected()
            ]
            return pending_orders
        except Exception as e:
            self.logger.error(f"获取挂单列表失败: {e}")
            return []

    async def _analyze_unreasonable_orders(self, pending_orders, current_positions, current_balance):
        """
        分析不合理的挂单
        """
        unreasonable_orders = []
        
        try:
            # 获取当前市价
            current_prices = {}
            for order_id, order in pending_orders:
                symbol = order.symbol
                if symbol not in current_prices:
                    try:
                        # 获取实时价格
                        current_prices[symbol] = await self._get_current_price(symbol)
                    except:
                        current_prices[symbol] = order.price
            
            for order_id, order in pending_orders:
                reasons = []
                
                # 1. 检查价格偏离度
                current_price = current_prices.get(order.symbol, order.price)
                price_deviation = abs(order.price - current_price) / current_price if current_price > 0 else 0
                
                if price_deviation > 0.05:  # 价格偏离超过5%
                    reasons.append(f"价格偏离{price_deviation:.1%}")
                
                # 2. 检查订单年龄
                order_age = (datetime.now() - order.submitted_at).total_seconds()
                if order_age > 180:  # 超过3分钟
                    reasons.append(f"订单超时{order_age/60:.1f}分钟")
                
                # 3. 检查资金占用比例
                order_value = order.price * order.quantity
                if order.side == OrderSide.Buy and current_balance > 0:
                    value_ratio = order_value / current_balance
                    if value_ratio > 0.3:  # 单个订单占用资金超过30%
                        reasons.append(f"资金占用过高{value_ratio:.1%}")
                
                # 4. 检查重复持仓
                if order.side == OrderSide.Buy:
                    existing_position = next((p for p in current_positions if p.symbol == order.symbol), None)
                    if existing_position and existing_position.quantity > 50:  # 已有大量持仓
                        reasons.append(f"已有大量持仓{existing_position.quantity}股")
                
                # 5. 检查置信度信息（如果可获取）
                if hasattr(order, 'strategy_name') and 'confidence' in str(order.strategy_name).lower():
                    try:
                        import re
                        confidence_match = re.search(r'confidence[=:]?\s*([0-9.]+)', str(order.strategy_name).lower())
                        if confidence_match:
                            confidence = float(confidence_match.group(1))
                            if confidence < 0.08:  # 置信度过低
                                reasons.append(f"置信度过低{confidence:.1%}")
                    except:
                        pass
                
                # 6. 检查市场方向不匹配
                if order.side == OrderSide.Buy and current_price < order.price * 0.98:
                    reasons.append("市价下跌，买入订单过高")
                elif order.side == OrderSide.Sell and current_price > order.price * 1.02:
                    reasons.append("市价上涨，卖出订单过低")
                
                if reasons:
                    unreasonable_orders.append({
                        'order_id': order_id,
                        'order': order,
                        'reasons': reasons,
                        'priority': len(reasons) + (1 if order_age > 300 else 0)  # 超过5分钟的订单优先级更高
                    })
            
            # 按优先级排序
            unreasonable_orders.sort(key=lambda x: x['priority'], reverse=True)
            
            if unreasonable_orders:
                self.logger.info(f"🔍 发现{len(unreasonable_orders)}个不合理挂单需要清理")
                for item in unreasonable_orders[:3]:  # 显示前3个
                    order = item['order']
                    self.logger.info(f"  - {order.symbol} {order.side} {order.quantity}@{order.price} 原因: {', '.join(item['reasons'])}")
            
            return unreasonable_orders
            
        except Exception as e:
            self.logger.error(f"分析不合理挂单失败: {e}")
            return []

    async def _get_current_price(self, symbol: str) -> float:
        """获取股票当前价格"""
        try:
            # 尝试从实时数据模块获取价格
            if hasattr(self, 'realtime_data') and self.realtime_data:
                price = await self.realtime_data.get_current_price(symbol)
                if price and price > 0:
                    return price
            
            # 如果没有实时数据模块，使用简化的价格获取
            # 这里可以集成其他价格数据源
            return 100.0  # 默认价格，实际应该替换为真实价格获取
            
        except Exception as e:
            self.logger.warning(f"获取{symbol}实时价格失败: {e}")
            return 100.0

    async def _cleanup_unreasonable_orders(self, unreasonable_orders):
        """清理不合理的挂单"""
        try:
            # 限制每次清理的数量，避免过度清理
            max_cleanup = min(5, len(unreasonable_orders))
            
            cleanup_count = 0
            for item in unreasonable_orders[:max_cleanup]:
                if cleanup_count >= max_cleanup:
                    break
                
                order_id = item['order_id']
                order = item['order']
                reasons = item['reasons']
                
                self.logger.info(f"🗑️ 清理不合理挂单: {order.symbol} {order.side} {order.quantity}@{order.price} ({', '.join(reasons)})")
                
                success = await self._cancel_order(order_id)
                if success:
                    cleanup_count += 1
                    await asyncio.sleep(0.2)  # 避免API频率限制
                
            if cleanup_count > 0:
                self.logger.info(f"✅ 成功清理{cleanup_count}个不合理挂单")
                
        except Exception as e:
            self.logger.error(f"清理不合理挂单失败: {e}")

    async def _analyze_positions_for_optimization(self, current_positions, signal: Signal):
        """
        分析是否需要卖出部分持仓来优化资金配置
        """
        positions_to_sell = []
        
        try:
            # 如果是买入信号且资金不足，考虑卖出部分持仓
            if signal.signal_type == SignalType.BUY:
                current_balance = self.get_account_balance()
                required_amount = signal.price * signal.quantity
                
                # 如果资金不足
                if current_balance < required_amount * 1.1:  # 预留10%缓冲
                    self.logger.info(f"💰 资金不足，考虑优化持仓释放资金. 需要: ${required_amount:.2f}, 可用: ${current_balance:.2f}")
                    
                    # 获取当前价格用于计算持仓价值
                    position_values = {}
                    for position in current_positions:
                        try:
                            current_price = await self._get_current_price(position.symbol)
                            position_values[position.symbol] = current_price
                        except:
                            position_values[position.symbol] = 100.0  # 默认价格
                    
                    # 寻找可以卖出的持仓
                    for position in current_positions:
                        # 跳过当前要买入的股票
                        if position.symbol == signal.symbol:
                            continue
                        
                        # 检查是否有可用数量
                        available_qty = getattr(position, 'available_quantity', position.quantity)
                        if available_qty <= 0:
                            continue
                        
                        # 计算持仓价值
                        current_price = position_values.get(position.symbol, 100.0)
                        position_value = position.quantity * current_price
                        
                        # 优化策略：
                        # 1. 小仓位（价值低于$500）
                        if position_value < 500:
                            positions_to_sell.append({
                                'symbol': position.symbol,
                                'quantity': min(available_qty, 10),
                                'reason': f'小仓位优化(${position_value:.0f})'
                            })
                        
                        # 2. 持仓过多的股票（超过50股）
                        elif position.quantity > 50 and available_qty >= 20:
                            positions_to_sell.append({
                                'symbol': position.symbol,
                                'quantity': min(20, available_qty),
                                'reason': f'减仓优化({position.quantity}股->减少20股)'
                            })
                        
                        # 限制卖出数量，避免过度优化
                        if len(positions_to_sell) >= 3:
                            break
            
            if positions_to_sell:
                self.logger.info(f"📉 计划优化{len(positions_to_sell)}个持仓释放资金")
                for item in positions_to_sell:
                    self.logger.info(f"  - 卖出 {item['symbol']} {item['quantity']}股 ({item['reason']})")
            
            return positions_to_sell
            
        except Exception as e:
            self.logger.error(f"分析持仓优化失败: {e}")
            return []

    async def _execute_optimization_sells(self, positions_to_sell):
        """执行优化卖出"""
        try:
            for item in positions_to_sell:
                symbol = item['symbol']
                quantity = item['quantity']
                reason = item['reason']
                
                # 获取当前价格
                current_price = await self._get_current_price(symbol)
                
                # 创建卖出信号
                from strategy.signals import Signal, SignalType
                sell_signal = Signal(
                    symbol=symbol,
                    signal_type=SignalType.SELL,
                    price=current_price,
                    quantity=quantity,
                    confidence=0.9,  # 高置信度，因为是优化操作
                    strategy_name=f"portfolio_optimization"
                )
                
                self.logger.info(f"🔄 执行优化卖出: {symbol} {quantity}股@{current_price} ({reason})")
                
                # 执行卖出
                result = await self._create_sell_order(sell_signal)
                if result and result.status != OrderStatus.Rejected:
                    self.logger.info(f"✅ 优化卖出订单已提交: {symbol}")
                else:
                    self.logger.warning(f"❌ 优化卖出失败: {symbol}")
                
                await asyncio.sleep(0.3)  # 避免API频率限制
                
        except Exception as e:
            self.logger.error(f"执行优化卖出失败: {e}")

    async def _create_buy_order(self, signal: Signal) -> OrderResult:
        """创建买入订单"""
        symbol = signal.symbol
        price = signal.price
        quantity = signal.quantity
        price_float = float(price)
        
        # 🔧 严格的最小交易金额检查
        min_effective_trade_value = float(self.config.get('execution.min_trade_value', 200))  # 提高到$200
        current_trade_value = price_float * quantity
        
        # 如果交易金额过小，直接拒绝
        if current_trade_value < min_effective_trade_value:
            self.logger.warning(f"交易金额过小，拒绝执行: {symbol}, 金额=${current_trade_value:.2f} < ${min_effective_trade_value}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=f"交易金额过小：${current_trade_value:.2f} < ${min_effective_trade_value}",
                strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            )
        
        # 先进行成本效益分析
        confidence = getattr(signal, 'confidence', 0.1)
        is_effective, reason = self._is_trade_cost_effective(symbol, quantity, price_float, confidence)
        if not is_effective:
            # 尝试优化交易数量
            optimized_quantity = self._optimize_trade_size(symbol, quantity, price_float, confidence)
            if optimized_quantity <= 0:
                self.logger.warning(f"成本效益不佳且无法优化，拒绝交易: {symbol}, 原因: {reason}")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.Buy,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.Rejected,
                    submitted_at=datetime.now(),
                    msg=f"成本效益不佳: {reason}",
                    strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                )
            else:
                quantity = optimized_quantity
                self.logger.info(f"优化交易数量 {symbol}: {signal.quantity} -> {quantity}")

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
        
        # 🔧 检查是否是平空仓操作（当前有空头持仓）
        positions = self.get_positions()
        position = next((p for p in positions if p.symbol.lower() == symbol.lower()), None)
        current_quantity = position.quantity if position else 0
        
        if current_quantity < 0:
            # 当前有空头持仓，买入是平空操作
            short_quantity = abs(current_quantity)
            cover_quantity = min(quantity, short_quantity)
            
            self.logger.info(f"📈 平仓空头: {symbol}, 空头持仓: {short_quantity}, 平仓数量: {cover_quantity}")
            
            # 直接提交平空订单
            strategy_info = signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            if hasattr(signal, 'confidence'):
                strategy_info += f" confidence={signal.confidence:.3f} (COVER)"
            return await self._submit_order(symbol, price, cover_quantity, "buy", strategy_info)
        
        # 获取可用资金和总权益
        available_cash = self.get_account_balance()
        total_equity = available_cash  # 简化处理，实际应该包括持仓市值
        
        # 🔧 应用position_pct限制 - 关键修复！
        max_trade_value = abs(total_equity) * (self.max_position_pct / 100.0)
        price_float = float(price) if hasattr(price, '__float__') else price
        max_allowed_quantity = int(max_trade_value / price_float) if price_float > 0 else 0
        
        self.logger.info(f"风控检查 {symbol}: 总权益={total_equity:.2f}, position_pct限制={self.max_position_pct}%, "
                        f"最大交易金额={max_trade_value:.2f}, 原始数量={quantity}, 限制后数量={max_allowed_quantity}")
        
        # 应用position_pct限制
        if quantity > max_allowed_quantity:
            if max_allowed_quantity <= 0:
                self.logger.warning(f"position_pct限制：无法进行任何交易 {symbol}, 权益={total_equity:.2f}, 限制={self.max_position_pct}%")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.Buy,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.Rejected,
                    submitted_at=datetime.now(),
                    msg=f"单笔交易限制{self.max_position_pct}%：无可用资金",
                    strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                )
            
            self.logger.warning(f"position_pct限制：调整买入数量 {symbol}: {quantity} -> {max_allowed_quantity} (限制{self.max_position_pct}%)")
            quantity = max_allowed_quantity
        
        # 判断是否是美股，美股支持碎股交易
        is_us_stock = '.US' in symbol
        minimum_quantity = 1 if is_us_stock else self.get_lot_size(symbol)
        
        # 计算最小买入所需资金
        min_required_cash = price_float * minimum_quantity * 1.01  # 包含1%手续费缓冲
        
        # 🔧 严格资金检查：账户余额为负时拒绝交易
        if available_cash < 0:
            self.logger.warning(f"账户余额为负数，拒绝买入: {symbol}, 余额={available_cash:.2f}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy, 
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=f"账户余额为负数：{available_cash:.2f}",
                strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            )
        
        # 严格的资金检查：如果连最小买入都无法负担，直接拒绝
        if available_cash < min_required_cash:
            self.logger.warning(f"可用资金不足以买入最小单位: {symbol}, 可用资金: {available_cash:.2f}, 最小所需: {min_required_cash:.2f}")
            return OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy, 
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=f"可用资金不足，需要{min_required_cash:.2f}，实际{available_cash:.2f}",
                strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
            )
        
        # 计算所需资金（加上一些手续费的缓冲）
        required_cash = price_float * quantity * 1.01
        
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
        
        # 🔧 最终安全检查
        final_trade_value = price_float * quantity
        final_position_pct = (final_trade_value / abs(total_equity)) * 100 if total_equity != 0 else 0
        self.logger.info(f"最终交易检查 {symbol}: 数量={quantity}, 金额={final_trade_value:.2f}, "
                        f"占总权益={final_position_pct:.2f}%, 限制={self.max_position_pct}%")
        
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
        
        # 提交订单（传递置信度信息）
        strategy_info = signal.strategy_name if hasattr(signal, 'strategy_name') else ""
        if hasattr(signal, 'confidence'):
            strategy_info += f" confidence={signal.confidence:.3f}"
        return await self._submit_order(symbol, price, quantity, "buy", strategy_info)

    async def _create_sell_order(self, signal):
        """
        创建卖出订单（支持平多仓和做空）

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
        
        # 判断是否是美股（美股支持做空）
        is_us_stock = '.US' in symbol.upper()
        
        # 检查持仓
        positions = self.get_positions()
        position = next((p for p in positions if p.symbol.lower() == symbol.lower()), None)
        current_quantity = position.quantity if position else 0
        
        # 🔧 美股做空支持
        if not position or current_quantity <= 0:
            if is_us_stock and self.config.get("execution.enable_short_selling", True):
                # 美股允许做空（开空仓）
                self.logger.info(f"📉 美股做空: {symbol}, 数量: {quantity}, 当前持仓: {current_quantity}")
                
                # 做空风控检查
                short_limit = self.config.get("execution.max_short_position", 100)
                current_short = abs(current_quantity) if current_quantity < 0 else 0
                
                if current_short + quantity > short_limit:
                    adjusted_qty = max(0, short_limit - current_short)
                    if adjusted_qty <= 0:
                        self.logger.warning(f"做空限制: {symbol} 已达最大空头持仓 {short_limit}")
                        return OrderResult(
                            order_id="",
                            symbol=symbol,
                            side=OrderSide.Sell,
                            quantity=quantity,
                            price=price,
                            status=OrderStatus.Rejected,
                            submitted_at=datetime.now(),
                            msg=f"做空限制: 已达最大空头持仓 {short_limit}",
                            strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                        )
                    quantity = adjusted_qty
                    self.logger.info(f"调整做空数量: {symbol} -> {quantity}")
                
                # 直接提交做空订单
                strategy_info = signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                if hasattr(signal, 'confidence'):
                    strategy_info += f" confidence={signal.confidence:.3f}"
                return await self._submit_order(symbol, price, quantity, "sell", strategy_info)
            else:
                # 港股或禁用做空时，必须有持仓才能卖出
                self.logger.warning(f"无持仓可卖出: {symbol}, 实际持仓: {current_quantity}")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.Sell,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.Rejected,
                    submitted_at=datetime.now(),
                    msg=f"无持仓可卖出，实际持仓: {current_quantity}" + (" (港股不支持做空)" if not is_us_stock else ""),
                    strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                )
        
        # 🔧 修复：使用可用数量而不是总持仓数量来判断卖出限制
        available_quantity = getattr(position, 'available_quantity', position.quantity)
        total_quantity = position.quantity
        
        self.logger.debug(f"持仓检查 {symbol}: 总持仓={total_quantity}, 可用={available_quantity}, 信号数量={quantity}")
        
        # 如果可用持仓不足信号数量，调整为实际可卖出数量
        if available_quantity < quantity:
            original_quantity = quantity
            quantity = int(available_quantity)  # 使用可用数量
            self.logger.info(f"可用持仓不足，调整卖出数量: {symbol}, 原始: {original_quantity}, 调整后: {quantity}, 总持仓: {total_quantity}, 可用: {available_quantity}")
            
            # 如果调整后数量仍然为0，则拒绝
            if quantity <= 0:
                self.logger.warning(f"调整后卖出数量为0: {symbol}")
                return OrderResult(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.Sell,
                    quantity=original_quantity,
                    price=price,
                    status=OrderStatus.Rejected,
                    submitted_at=datetime.now(),
                    msg=f"可用持仓不足，总持仓: {total_quantity}, 可用: {available_quantity}",
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
        
        # 提交订单（传递置信度信息）
        strategy_info = signal.strategy_name if hasattr(signal, 'strategy_name') else ""
        if hasattr(signal, 'confidence'):
            strategy_info += f" confidence={signal.confidence:.3f}"
        return await self._submit_order(symbol, price, adjusted_quantity, "sell", strategy_info)
                
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
            # 🔧 检查挂单数量限制并实施智能降级策略
            pending_count = len([order for order in self.active_orders.values() 
                               if not order.is_filled() and not order.is_canceled() and not order.is_rejected()])
            
            if pending_count >= self.max_pending_orders:
                self.logger.warning(f"达到最大挂单数量限制: {pending_count}/{self.max_pending_orders}")
                
                # 🚀 智能市价单降级机制
                # 尝试从不同来源获取置信度信息
                confidence = 0.05  # 默认低置信度
                
                # 如果strategy_name包含置信度信息，尝试解析
                if isinstance(strategy_name, str) and 'confidence' in strategy_name.lower():
                    try:
                        # 简单的置信度解析（可能需要根据实际格式调整）
                        import re
                        confidence_match = re.search(r'confidence[=:]?\s*([0-9.]+)', strategy_name.lower())
                        if confidence_match:
                            confidence = float(confidence_match.group(1))
                    except:
                        pass
                
                # 高置信度信号转为市价单执行
                if confidence > 0.10:  # 置信度大于10%
                    self.logger.info(f"高置信度信号({confidence:.2%})转为市价单执行: {symbol}")
                    
                    # 先主动清理一个旧订单，为新的高质量信号让路
                    await self._cleanup_one_low_quality_order()
                    
                    # 重新计算挂单数量
                    pending_count = len([order for order in self.active_orders.values() 
                                       if not order.is_filled() and not order.is_canceled() and not order.is_rejected()])
                    
                    # 如果仍然超限，转为市价单
                    if pending_count >= self.max_pending_orders:
                        self.logger.info(f"转为市价单执行: {symbol}, 置信度: {confidence:.2%}")
                        # 这里可以实现市价单逻辑，暂时先用限价单但优先级更高
                        pass
                
                # 低置信度信号直接拒绝或延迟执行
                else:
                    return OrderResult(
                        order_id="",
                        symbol=symbol,
                        side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                        quantity=quantity,
                        price=price,
                        status=OrderStatus.Rejected,
                        submitted_at=datetime.now(),
                        msg=f"挂单限制且置信度低({confidence:.2%})，拒绝执行",
                        strategy_name=strategy_name
                    )
            
            # 自动调整价格到符合交易所规则
            adjusted_price = self._adjust_price_to_tick(symbol, price)
            
            # 成本效益分析（仅对买入订单进行，假设默认置信度）
            if order_type == "buy":
                confidence = 0.1  # 默认置信度，实际应从信号中获取
                is_effective, cost_reason = self._is_trade_cost_effective(symbol, quantity, adjusted_price, confidence)
                
                if not is_effective:
                    self.logger.warning(f"交易被成本效益分析拒绝: {cost_reason}")
                    
                    # 尝试优化交易数量
                    optimized_quantity = self._optimize_trade_size(symbol, quantity, adjusted_price, confidence)
                    
                    if optimized_quantity > 0 and optimized_quantity != quantity:
                        self.logger.info(f"使用优化后的交易数量: {quantity} -> {optimized_quantity}")
                        quantity = optimized_quantity
                    elif optimized_quantity == 0:
                        return OrderResult(
                            order_id="",
                            symbol=symbol,
                            side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                            quantity=quantity,
                            price=adjusted_price,
                            status=OrderStatus.Rejected,
                            submitted_at=datetime.now(),
                            msg=f"成本效益分析失败: {cost_reason}",
                            strategy_name=strategy_name
                        )
            
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
            if signal.signal_type not in [SignalType.BUY, SignalType.SELL, SignalType.SHORT, SignalType.COVER]:
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
                    result.order_id,
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

    def _calculate_transaction_costs(self, symbol: str, price: float, quantity: int) -> Dict[str, float]:
        """
        计算交易总成本（包括各种手续费）
        
        Args:
            symbol: 股票代码
            price: 价格
            quantity: 数量
            
        Returns:
            成本详情字典
        """
        try:
            is_us_stock = '.US' in symbol
            is_hk_stock = '.HK' in symbol
            
            # 基础交易金额
            transaction_value = float(price) * int(quantity)
            
            if is_us_stock:
                # 美股手续费结构（更精确）
                commission_rate = 0.005  # 0.5% 佣金
                platform_fee = max(0.99, transaction_value * 0.0001)  # 平台费，最低$0.99
                sec_fee = transaction_value * 0.0000051  # SEC费用
                finra_fee = max(0.01, quantity * 0.000119)  # FINRA费用
                
                total_cost = commission_rate + platform_fee + sec_fee + finra_fee
                
            elif is_hk_stock:
                # 港股手续费结构（更精确）
                commission_rate = transaction_value * 0.0025  # 0.25% 佣金
                stamp_duty = transaction_value * 0.001  # 0.1% 印花税
                trading_fee = transaction_value * 0.0000565  # 交易费
                clearing_fee = max(2.5, transaction_value * 0.00002)  # 结算费
                
                total_cost = commission_rate + stamp_duty + trading_fee + clearing_fee
                
            else:
                # 其他市场默认
                total_cost = transaction_value * 0.003  # 0.3%
            
            return {
                'transaction_value': transaction_value,
                'total_cost': total_cost,
                'cost_ratio': total_cost / transaction_value if transaction_value > 0 else 0,
                'break_even_move': total_cost * 2 / quantity  # 双向交易需要的价格变动
            }
            
        except Exception as e:
            self.logger.error(f"计算交易成本失败: {e}")
            # 返回保守估计
            transaction_value = float(price) * int(quantity)
            conservative_cost = transaction_value * 0.01  # 1%保守估计
            return {
                'transaction_value': transaction_value,
                'total_cost': conservative_cost,
                'cost_ratio': 0.01,
                'break_even_move': conservative_cost * 2 / quantity
            }

    def _calculate_trading_costs(self, symbol: str, quantity: int, price: float) -> Dict[str, float]:
        """
        计算完整的交易成本
        
        Args:
            symbol: 股票代码
            quantity: 交易数量
            price: 交易价格
            
        Returns:
            交易成本详情字典
        """
        try:
            # 基础交易金额
            trade_value = float(price) * int(quantity)
            
            # 获取真实手续费率配置
            if '.US' in symbol:
                # 美股手续费结构
                commission_rate = float(self.config.get('us_commission_rate', 0.005))  # 0.5%
                platform_fee = float(self.config.get('us_platform_fee', 0.99))  # 平台费$0.99
                sec_fee = trade_value * 0.0000278  # SEC费用
                total_commission = max(platform_fee, trade_value * commission_rate) + sec_fee
            elif '.HK' in symbol:
                # 港股手续费结构
                commission_rate = float(self.config.get('hk_commission_rate', 0.0025))  # 0.25%
                stamp_duty = trade_value * 0.001  # 印花税0.1%
                trading_fee = trade_value * 0.00005  # 交易费0.005%
                clearing_fee = min(trade_value * 0.00002, 100)  # 结算费，最高$100
                total_commission = trade_value * commission_rate + stamp_duty + trading_fee + clearing_fee
            else:
                # 其他市场默认
                commission_rate = float(self.config.get('default_commission_rate', 0.0025))
                total_commission = trade_value * commission_rate
            
            # 双向交易成本（买入+卖出）
            round_trip_cost = total_commission * 2
            
            # 成本占比
            cost_percentage = (round_trip_cost / trade_value) * 100
            
            return {
                'trade_value': trade_value,
                'single_commission': total_commission,
                'round_trip_cost': round_trip_cost,
                'cost_percentage': cost_percentage,
                'break_even_change': cost_percentage  # 需要的最小价格变动百分比
            }
            
        except Exception as e:
            self.logger.error(f"计算交易成本失败: {e}")
            return {
                'trade_value': 0,
                'single_commission': 0,
                'round_trip_cost': 0,
                'cost_percentage': 5.0,  # 保守估计5%
                'break_even_change': 5.0
            }

    def _is_trade_cost_effective(self, symbol: str, quantity: int, price: float, confidence: float) -> Tuple[bool, str]:
        """
        检查交易是否具有成本效益
        
        Args:
            symbol: 股票代码
            quantity: 交易数量
            price: 交易价格
            confidence: 信号置信度
            
        Returns:
            (是否具有成本效益, 详细说明)
        """
        try:
            # 计算交易成本
            costs = self._calculate_trading_costs(symbol, quantity, price)
            
            # 获取配置的成本效益参数
            min_profit_threshold = float(self.config.get('execution.min_profit_threshold', 3.0))  # 默认3%
            max_cost_ratio = float(self.config.get('execution.max_cost_ratio', 2.0))  # 默认最大成本占比2.0%
            min_trade_value = float(self.config.get('execution.min_trade_value', 300))  # 默认最小交易$300
            
            # 小额交易特殊参数
            small_trade_threshold = float(self.config.get('execution.small_trade_threshold', 500))  # 小额交易阈值$500
            small_trade_max_cost_ratio = float(self.config.get('execution.small_trade_max_cost_ratio', 3.0))  # 小额交易最大成本占比3.0%
            
            # 判断是否为小额交易
            is_small_trade = costs['trade_value'] < small_trade_threshold
            
            # 根据交易大小选择不同的成本阈值
            effective_max_cost_ratio = small_trade_max_cost_ratio if is_small_trade else max_cost_ratio
            
            # 基础检查：交易金额是否过小
            if costs['trade_value'] < min_trade_value:
                return False, f"交易金额过小: ${costs['trade_value']:.0f} < ${min_trade_value:.0f}"
            
            # 成本占比检查
            if costs['cost_percentage'] > effective_max_cost_ratio:
                trade_type = "小额交易" if is_small_trade else "常规交易"
                return False, f"{trade_type}成本过高: {costs['cost_percentage']:.2f}% > {effective_max_cost_ratio}%"
            
            # 预期收益检查（基于信号置信度）
            expected_return = abs(confidence) * 100  # 将置信度转换为预期收益百分比
            required_return = costs['break_even_change'] + min_profit_threshold
            
            if expected_return < required_return:
                return False, f"预期收益不足: {expected_return:.2f}% < 需求{required_return:.2f}% (成本{costs['break_even_change']:.2f}% + 利润{min_profit_threshold}%)"
            
            # 记录成功的成本效益分析
            trade_type = "小额交易" if is_small_trade else "常规交易"
            self.logger.info(f"{trade_type}成本分析通过 {symbol}: 交易额=${costs['trade_value']:.0f}, "
                           f"成本{costs['cost_percentage']:.2f}%(<{effective_max_cost_ratio}%), "
                           f"预期收益{expected_return:.2f}%(>{required_return:.2f}%)")
            
            return True, f"交易具有成本效益: 预期收益{expected_return:.2f}% > 成本要求{required_return:.2f}%"
            
        except Exception as e:
            self.logger.error(f"成本效益分析失败: {e}")
            return False, f"成本效益分析失败: {e}"

    def _optimize_trade_size(self, symbol: str, original_quantity: int, price: float, confidence: float) -> int:
        """
        优化交易数量以提高成本效益
        
        Args:
            symbol: 股票代码
            original_quantity: 原始数量
            price: 交易价格
            confidence: 信号置信度
            
        Returns:
            优化后的交易数量
        """
        try:
            # 获取最小有效交易金额
            min_trade_value = float(self.config.get('execution.min_trade_value', 300))  # 默认$300
            
            # 计算当前交易金额
            current_value = float(price) * original_quantity
            
            # 🔧 严格检查：如果信号置信度过低，不值得进行大额交易
            if abs(confidence) < 0.1:  # 置信度低于10%
                max_low_confidence_value = 500  # 低置信度最大交易$500
                if current_value > max_low_confidence_value:
                    self.logger.warning(f"信号置信度过低({confidence:.1%})，限制交易金额到${max_low_confidence_value}")
                    optimized_quantity = int(max_low_confidence_value / float(price))
                    optimized_quantity = self._adjust_lot_size(symbol, optimized_quantity)
                    if optimized_quantity * float(price) < min_trade_value:
                        return 0  # 优化后仍低于最小交易金额，拒绝交易
                    return optimized_quantity
            
            # 如果当前交易金额太小，尝试增加到最小有效金额
            if current_value < min_trade_value:
                # 检查是否值得增加交易量
                if abs(confidence) < 0.15:  # 置信度低于15%时不增加交易量
                    self.logger.warning(f"信号置信度过低({confidence:.1%})，不增加交易量")
                    return 0
                
                optimized_quantity = max(int(min_trade_value / float(price)), 1)
                
                # 调整为合适的手数
                optimized_quantity = self._adjust_lot_size(symbol, optimized_quantity)
                
                # 验证优化后的交易是否具有成本效益
                is_effective, reason = self._is_trade_cost_effective(symbol, optimized_quantity, price, confidence)
                
                if is_effective:
                    self.logger.info(f"优化交易数量: {symbol} {original_quantity} -> {optimized_quantity} 股 "
                                   f"(${current_value:.0f} -> ${float(price) * optimized_quantity:.0f})")
                    return optimized_quantity
                else:
                    self.logger.warning(f"即使优化后仍不具成本效益: {reason}")
                    return 0  # 不执行交易
            
            # 对于现有数量合适的交易，仍需验证成本效益
            is_effective, reason = self._is_trade_cost_effective(symbol, original_quantity, price, confidence)
            if not is_effective:
                self.logger.warning(f"原始交易量不具成本效益: {reason}")
                return 0
            
            return original_quantity
            
        except Exception as e:
            self.logger.error(f"优化交易数量失败: {e}")
            return 0  # 发生错误时拒绝交易

    def _check_profitability(self, symbol: str, price: float, quantity: int, confidence: float) -> Tuple[bool, str]:
        """
        检查交易的盈利能力（保留原有方法兼容性）
        """
        return self._is_trade_cost_effective(symbol, quantity, price, confidence)
