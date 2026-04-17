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
from execution.position_service import PositionService
from execution.order_tracker import OrderTracker
from execution.order_executor import OrderExecutor
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
                 strategy_name: str = "",
                 signal_source: str = "",
                 signal_confidence: float = 0.0):
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
            signal_source: 触发策略来源（如 "volume_anomaly,ccass"），用于事后归因
            signal_confidence: 信号置信度（0~1），用于事后胜率统计
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
        self.signal_source = signal_source
        self.signal_confidence = float(signal_confidence) if signal_confidence else 0.0
        self.realized_pnl = 0.0
        self.reject_reason = ""
        
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
            "strategy_name": self.strategy_name,
            "signal_source": self.signal_source,
            "signal_confidence": self.signal_confidence,
            "realized_pnl": self.realized_pnl,
            "reject_reason": self.reject_reason,
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
        configured_lots = config_loader.get("execution.lot_sizes", {})
        self.min_quantity_unit = {**configured_lots, "AAPL.US": 1}
        
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
        
        # Pending order dedup: {symbol: {"side": str, "order_id": str, "submitted_at": datetime}}
        self._pending_signal_orders: Dict[str, dict] = {}
        self._pending_order_ttl = timedelta(minutes=30)
        
        # Symbols that the exchange rejected for short selling (e.g. 603301 error)
        self._short_blacklist: set = set()
        self._short_sell_order_ids: set = set()
        
        # 持仓加权平均成本追踪（用于 SELL 成交时计算 realized_pnl）
        # {symbol: {"qty": int, "cost": float}} —— qty 仅记录多头加权，简化版
        self._position_cost: Dict[str, Dict[str, float]] = {}
        
        # 🏗️ 初始化子服务
        self.position_service = PositionService(self)
        self.order_tracker = OrderTracker(self)
        self.order_executor = OrderExecutor(self)
        
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
        """启动订单状态跟踪任务 — delegates to OrderTracker"""
        self.order_tracker._start_order_tracking()

    # 处理信号的方法
    async def process_signal(self, signal: Signal, realtime_mgr=None) -> Optional[OrderResult]:
        """处理信号并执行交易"""
        return await self.execute_signal(signal, realtime_mgr=realtime_mgr)
        
    def register_order_callback(self, callback: Callable[[OrderResult], None]):
        """注册订单回调函数 — delegates to OrderTracker"""
        self.order_tracker.register_order_callback(callback)
    
    def _check_pending_dedup(self, symbol: str, side: str) -> bool:
        """Return True if a duplicate pending order exists for this symbol+side."""
        now = datetime.now()
        expired = [s for s, v in self._pending_signal_orders.items()
                   if now - v["submitted_at"] > self._pending_order_ttl]
        for s in expired:
            del self._pending_signal_orders[s]
        
        if symbol in self._pending_signal_orders:
            pending = self._pending_signal_orders[symbol]
            if pending["side"] == side:
                self.logger.info(
                    f"跳过重复订单: {symbol} {side} 已有待处理订单 "
                    f"(order_id={pending.get('order_id')}, "
                    f"提交于 {pending['submitted_at'].strftime('%H:%M:%S')})"
                )
                return True
        return False
    
    async def execute_signal(self, signal: Signal, realtime_mgr=None):
        """执行交易信号"""
        if not self._validate_risk_control(signal):
            self.logger.warning(f"风控检查不通过，拒绝执行信号: {signal}")
            return None
        
        try:
            self.logger.info(f"执行交易信号: {signal}")
            
            symbol = signal.symbol
            signal_type = signal.signal_type
            
            signal_type_str = signal_type.value if hasattr(signal_type, 'value') else str(signal_type)
            
            # SELL 无持仓时：根据配置决定是否自动转为 SHORT
            if signal_type == SignalType.SELL:
                existing_positions = self.get_positions(symbol)
                has_long = False
                if existing_positions:
                    for pos in existing_positions:
                        if getattr(pos, 'symbol', '').upper() == symbol.upper():
                            qty = int(getattr(pos, 'quantity', 0))
                            if qty > 0:
                                has_long = True
                                break
                if not has_long:
                    is_us = symbol.upper().endswith('.US')
                    allow_auto_short = self.config.get("execution.allow_sell_to_short", False)
                    if allow_auto_short and is_us and symbol not in self._short_blacklist:
                        self.logger.info(f"🔄 {symbol} 无多头持仓，SELL 自动转为 SHORT (美股做空)")
                        signal_type = SignalType.SHORT
                        signal.signal_type = SignalType.SHORT
                        signal_type_str = "SHORT"
                    else:
                        if symbol in self._short_blacklist:
                            reason = "在做空黑名单中"
                        elif not is_us:
                            reason = "非美股，不支持做空"
                        else:
                            reason = "allow_sell_to_short=false"
                        self.logger.info(f"⚠️ {symbol} 无多头持仓，SELL 信号跳过 ({reason})")
                        return None
            
            if signal_type in [SignalType.BUY, SignalType.COVER]:
                side_str = "Buy"
            elif signal_type == SignalType.SELL:
                side_str = "Sell"
            elif signal_type == SignalType.SHORT:
                side_str = "Short"
            else:
                side_str = "Hold"
            
            if signal_type not in [SignalType.HOLD, SignalType.UNKNOWN] and \
               self._check_pending_dedup(symbol, side_str):
                return None
            
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
                actual_side_str = result.side.name if hasattr(result.side, 'name') else str(result.side)
                status_str = result.status.name if hasattr(result.status, 'name') else str(result.status)
                self.logger.info(f"✅ 订单已提交: {result.order_id}, 方向={actual_side_str}, 状态={status_str}")
                
                self._pending_signal_orders[symbol] = {
                    "side": side_str,
                    "order_id": getattr(result, 'order_id', None),
                    "submitted_at": datetime.now()
                }
                
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
                        current_prices[symbol] = await self._get_current_price(symbol)
                        if current_prices[symbol] <= 0:
                            current_prices[symbol] = order.price
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
            if hasattr(self, 'realtime_data') and self.realtime_data:
                price = await self.realtime_data.get_current_price(symbol)
                if price and price > 0:
                    return price
            
            try:
                from longport.openapi import QuoteContext as QCtx
                quote_ctx = QCtx(self.longport_config)
                quotes = quote_ctx.quote([symbol])
                if quotes:
                    p = float(quotes[0].last_done)
                    if p > 0:
                        return p
            except Exception:
                pass
            
            return 0.0
            
        except Exception as e:
            self.logger.warning(f"获取{symbol}实时价格失败: {e}")
            return 0.0

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
            sig_src, sig_conf = self._extract_signal_meta(signal)
            return await self._submit_order(symbol, price, cover_quantity, "buy", strategy_info,
                                             signal_source=sig_src, signal_confidence=sig_conf)
        
        # 获取可用资金和总权益（优先使用券商 net_assets）
        available_cash = self.get_account_balance()
        margin_info = self.get_margin_info()
        if margin_info and margin_info.get("available") and margin_info["net_assets"] > 0:
            total_equity = margin_info["net_assets"]
        else:
            total_equity = available_cash
        
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
        sig_src, sig_conf = self._extract_signal_meta(signal)
        return await self._submit_order(symbol, price, quantity, "buy", strategy_info,
                                         signal_source=sig_src, signal_confidence=sig_conf)

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
        
        if not position or current_quantity <= 0:
            if symbol.upper() in self._short_blacklist:
                self.logger.warning(f"做空黑名单: {symbol} 交易所不支持做空")
                return OrderResult(
                    order_id="", symbol=symbol, side=OrderSide.Sell,
                    quantity=quantity, price=price,
                    status=OrderStatus.Rejected, submitted_at=datetime.now(),
                    msg=f"做空黑名单: {symbol} 不支持做空",
                    strategy_name=signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                )
            
            if self.config.get("execution.enable_short_selling", True):
                market_label = "美股" if is_us_stock else "港股"
                self.logger.info(f"📉 {market_label}做空: {symbol}, 数量: {quantity}, 当前持仓: {current_quantity}")
                
                short_limit = self.config.get("execution.max_short_position", 500)
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
                
                strategy_info = signal.strategy_name if hasattr(signal, 'strategy_name') else ""
                if hasattr(signal, 'confidence'):
                    strategy_info += f" confidence={signal.confidence:.3f}"
                sig_src, sig_conf = self._extract_signal_meta(signal)
                result = await self._submit_order(symbol, price, quantity, "sell", strategy_info,
                                                   signal_source=sig_src, signal_confidence=sig_conf)
                if result and result.order_id:
                    self._short_sell_order_ids.add(result.order_id)
                return result
            else:
                self.logger.warning(f"做空已禁用，无持仓可卖出: {symbol}, 实际持仓: {current_quantity}")
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
        sig_src, sig_conf = self._extract_signal_meta(signal)
        return await self._submit_order(symbol, price, adjusted_quantity, "sell", strategy_info,
                                         signal_source=sig_src, signal_confidence=sig_conf)
                
    # ── Delegation to OrderExecutor ──

    async def _submit_order(self, symbol, price, quantity, order_type, strategy_name,
                             signal_source: str = "", signal_confidence: float = 0.0):
        return await self.order_executor._submit_order(
            symbol, price, quantity, order_type, strategy_name,
            signal_source=signal_source, signal_confidence=signal_confidence,
        )

    def get_lot_size(self, symbol: str) -> int:
        return self.order_executor.get_lot_size(symbol)

    def _adjust_price_to_tick(self, symbol, price):
        return self.order_executor._adjust_price_to_tick(symbol, price)

    def _adjust_lot_size(self, symbol, quantity):
        return self.order_executor._adjust_lot_size(symbol, quantity)

    def _get_hk_price_tick(self, price):
        return self.order_executor._get_hk_price_tick(price)

    def _validate_order_parameters(self, symbol, price, quantity):
        return self.order_executor._validate_order_parameters(symbol, price, quantity)

    async def cancel_order(self, order_id: str) -> bool:
        return await self.order_executor.cancel_order(order_id)

    # ── Delegation to OrderTracker ──

    async def get_order_status(self, order_id: str) -> Optional[OrderResult]:
        return await self.order_tracker.get_order_status(order_id)

    async def get_today_orders(self, symbol: str = None):
        return await self.order_tracker.get_today_orders(symbol)

    def _notify_order_update(self, order):
        self.order_tracker._notify_order_update(order)

    def _save_order_to_csv(self, result):
        self.order_tracker._save_order_to_csv(result)
    
    # ── 持仓加权平均成本追踪（用于 realized_pnl 计算） ──
    
    def update_position_cost_on_fill(self, order: 'OrderResult', filled_price: float, filled_qty: int):
        """订单成交时更新持仓加权平均成本，并对 SELL 计算 realized_pnl。
        
        简化模型：
        - BUY 成交：qty += filled_qty，cost = (old_cost*old_qty + filled_price*filled_qty) / new_qty
        - SELL 成交：realized_pnl = (filled_price - avg_cost) * filled_qty；qty -= filled_qty
        - 仅追踪多头；空头不在此模型内（设 realized_pnl=0，需要时另行扩展）
        """
        try:
            from longport.openapi import OrderSide
            symbol = order.symbol
            entry = self._position_cost.setdefault(symbol, {"qty": 0.0, "cost": 0.0})
            
            if order.side == OrderSide.Buy:
                old_qty = entry["qty"]
                old_cost = entry["cost"]
                new_qty = old_qty + filled_qty
                if new_qty > 0:
                    entry["cost"] = (old_cost * old_qty + filled_price * filled_qty) / new_qty
                    entry["qty"] = new_qty
                order.realized_pnl = 0.0
            elif order.side == OrderSide.Sell and entry["qty"] > 0:
                avg_cost = entry["cost"]
                pnl = (filled_price - avg_cost) * filled_qty
                order.realized_pnl = float(round(pnl, 4))
                entry["qty"] = max(0.0, entry["qty"] - filled_qty)
                if entry["qty"] <= 0:
                    entry["cost"] = 0.0
            else:
                order.realized_pnl = 0.0
        except Exception as e:
            self.logger.debug(f"更新持仓成本/计算 PnL 失败 {order.symbol}: {e}")
    
    def seed_position_cost(self, symbol: str, qty: float, cost_price: float):
        """启动时从 broker 同步持仓 → 初始化加权平均成本。"""
        if qty <= 0 or cost_price <= 0:
            return
        self._position_cost[symbol] = {"qty": float(qty), "cost": float(cost_price)}
    
    # ── Delegation to PositionService ──

    def get_account_balance(self):
        return self.position_service.get_account_balance()

    def get_margin_info(self):
        return self.position_service.get_margin_info()

    def get_positions(self, symbol: str = None):
        return self.position_service.get_positions(symbol)

    async def get_position(self, symbol: str):
        return await self.position_service.get_position(symbol)
    
    def seed_position_costs_from_broker(self):
        """启动时同步 broker 持仓的成本价到本地加权平均成本表，
        供后续 SELL 成交时计算 realized_pnl。"""
        try:
            positions = self.get_positions(None)
            if not positions:
                return 0
            seeded = 0
            for pos in positions:
                sym = getattr(pos, 'symbol', None)
                qty = float(getattr(pos, 'quantity', 0) or 0)
                cost = getattr(pos, 'cost_price', None)
                if not sym or qty <= 0 or cost is None:
                    continue
                self.seed_position_cost(sym, qty, float(cost))
                seeded += 1
            if seeded:
                self.logger.info(f"已从 broker 同步 {seeded} 个标的的初始成本价用于 PnL 追踪")
            return seeded
        except Exception as e:
            self.logger.warning(f"从 broker 同步成本价失败: {e}")
            return 0
    
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
        self.order_tracker._save_order(order_result, strategy_name)

    def _check_daily_order_limit(self) -> bool:
        return self.order_tracker._check_daily_order_limit()

    def is_enough_balance(self, cost: float, symbol: str = None) -> bool:
        return self.position_service.is_enough_balance(cost, symbol)

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

    async def submit_buy_order(self, symbol, price, quantity, strategy_name="default",
                                signal_source: str = "", signal_confidence: float = 0.0):
        return await self.order_executor.submit_buy_order(
            symbol, price, quantity, strategy_name,
            signal_source=signal_source, signal_confidence=signal_confidence,
        )

    async def submit_sell_order(self, symbol, price, quantity, strategy_name="default",
                                 signal_source: str = "", signal_confidence: float = 0.0):
        return await self.order_executor.submit_sell_order(
            symbol, price, quantity, strategy_name,
            signal_source=signal_source, signal_confidence=signal_confidence,
        )
    
    @staticmethod
    def _extract_signal_meta(signal):
        """从 Signal 对象提取 (signal_source, signal_confidence) 用于 CSV 归因。"""
        confidence = float(getattr(signal, 'confidence', 0.0) or 0.0)
        extra = getattr(signal, 'extra_data', None) or {}
        triggers = extra.get('trigger_sources', []) if isinstance(extra, dict) else []
        if not triggers:
            sname = getattr(signal, 'strategy_name', '') or ''
            triggers = [sname] if sname else []
        if isinstance(triggers, (list, tuple, set)):
            source = ",".join(str(t) for t in triggers if t)
        else:
            source = str(triggers)
        return source, confidence

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

    async def place_order(self, symbol, side, quantity, price_type="LIMIT", price=None):
        return await self.order_executor.place_order(symbol, side, quantity, price_type, price)

    # ── Delegation to OrderExecutor (cost analysis) ──

    def _calculate_transaction_costs(self, symbol, price, quantity):
        return self.order_executor._calculate_transaction_costs(symbol, price, quantity)

    def _calculate_trading_costs(self, symbol, quantity, price):
        return self.order_executor._calculate_trading_costs(symbol, quantity, price)

    def _is_trade_cost_effective(self, symbol, quantity, price, confidence):
        return self.order_executor._is_trade_cost_effective(symbol, quantity, price, confidence)

    def _optimize_trade_size(self, symbol, original_quantity, price, confidence):
        return self.order_executor._optimize_trade_size(symbol, original_quantity, price, confidence)

    def _check_profitability(self, symbol, price, quantity, confidence):
        return self.order_executor._check_profitability(symbol, price, quantity, confidence)

    # ── Delegation to PositionService (account info) ──

    def get_account_info(self):
        return self.position_service.get_account_info()

    def _create_default_account_info(self):
        return PositionService._create_default_account_info()