#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
量化交易系统 - 交易执行层服务器

此模块提供交易执行服务，包括：
1. 交易账户管理
2. 订单执行与监控
3. 仓位管理
4. 风险控制

使用方法：
    conda activate trading_execution_env
    python trading_server.py
"""

import os
import sys
import json
import logging
import datetime
import time
from typing import List, Dict, Any, Optional, Union
import traceback
import threading

# 检查是否安装了FastAPI
try:
    import fastapi
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    print("未安装FastAPI，无法启动API服务。如需API服务，请安装: pip install fastapi uvicorn")

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 导入共享模块
from common.shared_paths import (
    save_dataframe, load_dataframe, save_signal, load_signal,
    LOG_DIR, DATA_DIR, SIGNAL_DIR, api_request
)

# 导入必要库
import pandas as pd
import numpy as np
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, f"trading_server_{datetime.datetime.now().strftime('%Y%m%d')}.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("trading_server")

# 加载环境变量
load_dotenv()

# ======== 数据模型定义 ========

class Account(BaseModel):
    """交易账户模型"""
    account_id: str
    name: str = ""
    type: str = "stock"  # stock, future, option, crypto
    balance: float = 0.0
    positions: Dict[str, Dict[str, Any]] = {}
    orders: Dict[str, Dict[str, Any]] = {}
    status: str = "active"
    
class Order(BaseModel):
    """订单模型"""
    order_id: str
    account_id: str
    symbol: str
    direction: str  # buy, sell
    price: float
    quantity: int
    order_type: str = "limit"  # market, limit, stop
    status: str = "pending"  # pending, filled, partial, canceled, rejected
    create_time: str = Field(default_factory=lambda: datetime.datetime.now().isoformat())
    update_time: Optional[str] = None
    filled_quantity: int = 0
    average_price: Optional[float] = None
    remark: str = ""
    
class Position(BaseModel):
    """持仓模型"""
    account_id: str
    symbol: str
    quantity: int
    average_cost: float
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    realized_pnl: float = 0.0
    update_time: str = Field(default_factory=lambda: datetime.datetime.now().isoformat())

class TradeSignal(BaseModel):
    """交易信号模型"""
    signal_id: str
    strategy_id: str
    symbol: str
    direction: str  # buy, sell
    price: Optional[float] = None
    quantity: Optional[int] = None
    weight: Optional[float] = None  # 权重，用于资金分配
    reason: str = ""
    priority: int = 0  # 信号优先级
    expire_time: Optional[str] = None
    create_time: str = Field(default_factory=lambda: datetime.datetime.now().isoformat())

# ======== 交易接口抽象 ========

class TradingInterface:
    """交易接口抽象类"""
    
    def __init__(self):
        self.accounts = {}
        self.orders = {}
        self.positions = {}
        logger.info(f"{self.__class__.__name__} 初始化")
    
    def get_account(self, account_id: str) -> Dict[str, Any]:
        """获取账户信息"""
        raise NotImplementedError
    
    def get_positions(self, account_id: str) -> List[Dict[str, Any]]:
        """获取持仓信息"""
        raise NotImplementedError
    
    def get_orders(self, account_id: str, status: str = None) -> List[Dict[str, Any]]:
        """获取订单信息"""
        raise NotImplementedError
    
    def place_order(self, account_id: str, symbol: str, direction: str, 
                   quantity: int, price: float = None, order_type: str = "limit") -> Dict[str, Any]:
        """下单"""
        raise NotImplementedError
    
    def cancel_order(self, account_id: str, order_id: str) -> bool:
        """撤单"""
        raise NotImplementedError
    
    def get_market_data(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """获取市场行情"""
        raise NotImplementedError 

# ======== 交易接口实现 ========

class MockTradingInterface(TradingInterface):
    """模拟交易接口"""
    
    def __init__(self, initial_balance: float = 1000000.0):
        super().__init__()
        
        # 创建模拟账户
        account_id = f"mock_{int(time.time())}"
        self.accounts[account_id] = {
            "account_id": account_id,
            "name": "模拟交易账户",
            "type": "stock",
            "balance": initial_balance,
            "positions": {},
            "orders": {},
            "status": "active",
        }
        
        self.default_account_id = account_id
        logger.info(f"模拟交易接口初始化，账户ID: {account_id}，初始资金: {initial_balance}")
        
        # 市场数据缓存
        self.market_data = {}
        self.last_update_time = datetime.datetime.now()
    
    def get_account(self, account_id: str = None) -> Dict[str, Any]:
        """获取账户信息"""
        if account_id is None:
            account_id = self.default_account_id
            
        if account_id in self.accounts:
            # 更新账户市值
            account = self.accounts[account_id]
            total_value = account["balance"]
            
            for symbol, position in account["positions"].items():
                # 获取最新价格
                current_price = self._get_latest_price(symbol)
                
                # 更新持仓市值
                position["current_price"] = current_price
                position["market_value"] = position["quantity"] * current_price
                position["unrealized_pnl"] = position["market_value"] - position["quantity"] * position["average_cost"]
                
                # 更新账户总市值
                total_value += position["market_value"]
            
            # 添加总资产信息
            account["total_value"] = total_value
            account["update_time"] = datetime.datetime.now().isoformat()
            
            return account
        else:
            logger.error(f"账户不存在: {account_id}")
            return None
    
    def get_positions(self, account_id: str = None) -> List[Dict[str, Any]]:
        """获取持仓信息"""
        if account_id is None:
            account_id = self.default_account_id
            
        if account_id in self.accounts:
            account = self.accounts[account_id]
            positions = []
            
            for symbol, position in account["positions"].items():
                # 获取最新价格
                current_price = self._get_latest_price(symbol)
                
                # 更新持仓信息
                position["current_price"] = current_price
                position["market_value"] = position["quantity"] * current_price
                position["unrealized_pnl"] = position["market_value"] - position["quantity"] * position["average_cost"]
                position["update_time"] = datetime.datetime.now().isoformat()
                
                positions.append(position)
            
            return positions
        else:
            logger.error(f"账户不存在: {account_id}")
            return []
    
    def get_orders(self, account_id: str = None, status: str = None) -> List[Dict[str, Any]]:
        """获取订单信息"""
        if account_id is None:
            account_id = self.default_account_id
            
        if account_id in self.accounts:
            account = self.accounts[account_id]
            orders = list(account["orders"].values())
            
            # 根据状态过滤
            if status:
                orders = [order for order in orders if order["status"] == status]
            
            return sorted(orders, key=lambda x: x["create_time"], reverse=True)
        else:
            logger.error(f"账户不存在: {account_id}")
            return []
    
    def place_order(self, account_id: str, symbol: str, direction: str, 
                   quantity: int, price: float = None, order_type: str = "limit") -> Dict[str, Any]:
        """下单"""
        if account_id is None:
            account_id = self.default_account_id
            
        if account_id not in self.accounts:
            logger.error(f"账户不存在: {account_id}")
            return {"success": False, "error": "账户不存在"}
        
        # 获取账户信息
        account = self.accounts[account_id]
        
        # 生成订单ID
        order_id = f"{account_id}_{int(time.time())}_{len(account['orders']) + 1}"
        
        # 获取最新价格
        current_price = self._get_latest_price(symbol)
        
        # 如果是市价单，使用当前价格
        if order_type == "market" or price is None:
            price = current_price
        
        # 创建订单
        order = {
            "order_id": order_id,
            "account_id": account_id,
            "symbol": symbol,
            "direction": direction,
            "price": price,
            "quantity": quantity,
            "order_type": order_type,
            "status": "pending",
            "create_time": datetime.datetime.now().isoformat(),
            "update_time": None,
            "filled_quantity": 0,
            "average_price": None,
            "remark": "",
        }
        
        # 保存订单
        account["orders"][order_id] = order
        
        # 模拟订单执行
        self._process_order(order_id)
        
        return {"success": True, "order_id": order_id, "order": order}
    
    def cancel_order(self, account_id: str, order_id: str) -> bool:
        """撤单"""
        if account_id is None:
            account_id = self.default_account_id
            
        if account_id not in self.accounts:
            logger.error(f"账户不存在: {account_id}")
            return False
        
        account = self.accounts[account_id]
        
        if order_id not in account["orders"]:
            logger.error(f"订单不存在: {order_id}")
            return False
        
        order = account["orders"][order_id]
        
        # 只能撤销未完全成交的订单
        if order["status"] in ["pending", "partial"]:
            order["status"] = "canceled"
            order["update_time"] = datetime.datetime.now().isoformat()
            order["remark"] = "用户撤单"
            logger.info(f"订单已撤销: {order_id}")
            return True
        else:
            logger.warning(f"无法撤销状态为 {order['status']} 的订单: {order_id}")
            return False
    
    def get_market_data(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """获取市场行情"""
        result = {}
        
        # 检查是否需要更新数据
        now = datetime.datetime.now()
        if (now - self.last_update_time).total_seconds() > 60:
            self._update_market_data()
        
        # 返回请求的股票行情
        for symbol in symbols:
            if symbol in self.market_data:
                result[symbol] = self.market_data[symbol]
            else:
                # 生成模拟数据
                price = 100 + np.random.normal(0, 10)
                result[symbol] = {
                    "symbol": symbol,
                    "price": price,
                    "open": price * (1 - np.random.random() * 0.02),
                    "high": price * (1 + np.random.random() * 0.02),
                    "low": price * (1 - np.random.random() * 0.02),
                    "volume": int(np.random.random() * 10000000),
                    "time": now.isoformat(),
                }
                self.market_data[symbol] = result[symbol]
        
        return result
    
    def _get_latest_price(self, symbol: str) -> float:
        """获取最新价格"""
        # 检查缓存
        if symbol in self.market_data:
            return self.market_data[symbol]["price"]
        
        # 从数据服务获取
        try:
            # 尝试从数据服务获取价格
            response = api_request(f"/api/stocks/{symbol}/daily", params={"limit": 1})
            if response and len(response) > 0:
                latest_bar = response[0]
                return latest_bar["close"]
        except Exception as e:
            logger.warning(f"从数据服务获取价格失败: {e}")
        
        # 生成模拟价格
        price = 100 + np.random.normal(0, 10)
        self.market_data[symbol] = {
            "symbol": symbol,
            "price": price,
            "time": datetime.datetime.now().isoformat(),
        }
        return price
    
    def _update_market_data(self):
        """更新市场数据"""
        # 更新所有缓存的行情
        for symbol in list(self.market_data.keys()):
            data = self.market_data[symbol]
            
            # 模拟价格波动
            price_change = np.random.normal(0, data["price"] * 0.005)
            new_price = data["price"] + price_change
            
            # 更新数据
            data["price"] = new_price
            data["high"] = max(data.get("high", new_price), new_price)
            data["low"] = min(data.get("low", new_price), new_price)
            data["volume"] = data.get("volume", 0) + int(np.random.random() * 100000)
            data["time"] = datetime.datetime.now().isoformat()
        
        self.last_update_time = datetime.datetime.now()
    
    def _process_order(self, order_id: str):
        """模拟处理订单"""
        # 查找订单
        account_id = order_id.split("_")[0]
        account = self.accounts.get(account_id)
        
        if not account or order_id not in account["orders"]:
            logger.error(f"处理订单时找不到订单: {order_id}")
            return
        
        order = account["orders"][order_id]
        
        # 只处理待成交的订单
        if order["status"] not in ["pending", "partial"]:
            return
        
        # 获取最新价格
        symbol = order["symbol"]
        current_price = self._get_latest_price(symbol)
        
        # 检查是否能够成交
        can_execute = False
        
        if order["order_type"] == "market":
            # 市价单总是成交
            can_execute = True
            execution_price = current_price
        elif order["order_type"] == "limit":
            # 限价单检查价格
            if order["direction"] == "buy" and current_price <= order["price"]:
                can_execute = True
                execution_price = min(current_price, order["price"])
            elif order["direction"] == "sell" and current_price >= order["price"]:
                can_execute = True
                execution_price = max(current_price, order["price"])
            else:
                logger.debug(f"限价单未能成交: {order_id}, 方向: {order['direction']}, 订单价: {order['price']}, 当前价: {current_price}")
        
        # 如果能够成交
        if can_execute:
            # 计算成交数量
            remaining = order["quantity"] - order["filled_quantity"]
            execution_quantity = remaining
            
            # 检查买入时的资金是否足够
            if order["direction"] == "buy":
                cost = execution_quantity * execution_price
                if cost > account["balance"]:
                    # 资金不足，调整成交数量
                    execution_quantity = int(account["balance"] / execution_price)
                    logger.warning(f"资金不足，调整成交数量: {order_id}, 原数量: {remaining}, 调整后: {execution_quantity}")
            
            # 如果有可成交数量
            if execution_quantity > 0:
                # 更新订单
                order["filled_quantity"] += execution_quantity
                
                if order["filled_quantity"] == order["quantity"]:
                    order["status"] = "filled"
                else:
                    order["status"] = "partial"
                
                # 计算平均成交价
                if order["average_price"] is None:
                    order["average_price"] = execution_price
                else:
                    total_cost = order["average_price"] * (order["filled_quantity"] - execution_quantity) + execution_price * execution_quantity
                    order["average_price"] = total_cost / order["filled_quantity"]
                
                order["update_time"] = datetime.datetime.now().isoformat()
                
                # 更新账户余额和持仓
                if order["direction"] == "buy":
                    # 买入：减少余额，增加持仓
                    cost = execution_quantity * execution_price
                    account["balance"] -= cost
                    
                    # 更新持仓
                    if symbol not in account["positions"]:
                        account["positions"][symbol] = {
                            "account_id": account_id,
                            "symbol": symbol,
                            "quantity": execution_quantity,
                            "average_cost": execution_price,
                            "current_price": current_price,
                            "market_value": execution_quantity * current_price,
                            "unrealized_pnl": execution_quantity * (current_price - execution_price),
                            "realized_pnl": 0.0,
                            "update_time": datetime.datetime.now().isoformat(),
                        }
                    else:
                        position = account["positions"][symbol]
                        new_quantity = position["quantity"] + execution_quantity
                        position["average_cost"] = (position["quantity"] * position["average_cost"] + execution_quantity * execution_price) / new_quantity
                        position["quantity"] = new_quantity
                        position["market_value"] = new_quantity * current_price
                        position["unrealized_pnl"] = new_quantity * (current_price - position["average_cost"])
                        position["update_time"] = datetime.datetime.now().isoformat()
                
                elif order["direction"] == "sell":
                    # 卖出：增加余额，减少持仓
                    revenue = execution_quantity * execution_price
                    account["balance"] += revenue
                    
                    # 更新持仓
                    if symbol in account["positions"]:
                        position = account["positions"][symbol]
                        
                        # 计算实现盈亏
                        realized_pnl = execution_quantity * (execution_price - position["average_cost"])
                        position["realized_pnl"] += realized_pnl
                        
                        # 更新持仓数量
                        position["quantity"] -= execution_quantity
                        
                        # 如果数量为0，删除持仓
                        if position["quantity"] <= 0:
                            del account["positions"][symbol]
                        else:
                            # 更新市值和未实现盈亏
                            position["market_value"] = position["quantity"] * current_price
                            position["unrealized_pnl"] = position["quantity"] * (current_price - position["average_cost"])
                            position["update_time"] = datetime.datetime.now().isoformat()
                    else:
                        logger.warning(f"卖出不存在的持仓: {order_id}, 股票: {symbol}")
                
                logger.info(f"订单成交: {order_id}, 成交数量: {execution_quantity}, 成交价: {execution_price}")
            
        # 如果是市价单且未完全成交，继续尝试
        if order["order_type"] == "market" and order["status"] == "partial":
            # 递归调用自己，直到完全成交
            self._process_order(order_id)


try:
    from longport.openapi import QuoteContext, TradeContext, Config
    from longport.openapi.quote import (
        StockQuote, SecurityStaticInfo, MarketTradingSession,
        TradeStatus, PrePostQuote, SecurityDepth, BrokerQueue,
        OptionQuote, WarrantQuote, SecurityTradingSession
    )
    from longport.openapi.trade import (
        SubmitOrderRequest, OrderSide, TimeInForceType, OrderType,
        OrderStatus, ExecutionStyle, CashInfo
    )
    
    LONGPORT_AVAILABLE = True
except ImportError:
    LONGPORT_AVAILABLE = False
    logger.warning("未安装LongPort SDK，无法使用长桥接口")

class LongPortInterface(TradingInterface):
    """长桥证券交易接口"""
    
    def __init__(self):
        """初始化长桥证券交易接口"""
        super().__init__()
        self.name = "longport"
        self.logger = logging.getLogger("LongPortInterface")
        
        # 尝试导入LongPort SDK
        try:
            # 导入自定义客户端
            sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
            from longbridge_quant.api_client.client import LongPortClient
            from longport.openapi import (
                OrderSide, OrderType, TimeInForceType, Period, AdjustType, 
                SubType, Market, OrderStatus
            )
            
            self.LongPortClient = LongPortClient
            self.OrderSide = OrderSide
            self.OrderType = OrderType
            self.TimeInForceType = TimeInForceType
            self.Period = Period
            self.AdjustType = AdjustType
            self.SubType = SubType
            self.Market = Market
            self.OrderStatus = OrderStatus
            
            self.logger.info("成功导入LongPort SDK")
            
            # 初始化客户端
            self.client = LongPortClient(use_websocket=True)
            
            # 验证连接
            try:
                # 尝试获取账户信息，验证连接是否正常
                _ = self.client.trade_ctx
                _ = self.client.quote_ctx
                self.logger.info("长桥API连接成功")
                self.connected = True
            except Exception as e:
                self.logger.error(f"长桥API连接失败: {str(e)}")
                self.connected = False
                raise
                
        except ImportError as e:
            self.logger.error(f"未安装LongPort SDK，无法使用LongPort交易接口")
            raise ImportError("未安装LongPort SDK，请先安装: pip install longbridge")
        except Exception as e:
            self.logger.error(f"初始化LongPort客户端失败: {str(e)}")
            self.connected = False
            raise
            
        # 缓存
        self._account_cache = {}
        self._positions_cache = {}
        self._orders_cache = {}
        self._last_refresh = {
            "account": datetime.datetime.now() - datetime.timedelta(minutes=10),
            "positions": datetime.datetime.now() - datetime.timedelta(minutes=10),
            "orders": datetime.datetime.now() - datetime.timedelta(minutes=10),
        }
        self.cache_ttl = 5  # 缓存有效期(秒)
        
    def is_cache_valid(self, cache_type):
        """检查缓存是否有效"""
        now = datetime.datetime.now()
        return (now - self._last_refresh[cache_type]).total_seconds() < self.cache_ttl
        
    def refresh_cache(self, cache_type):
        """刷新缓存时间"""
        self._last_refresh[cache_type] = datetime.datetime.now()
        
    def get_account_info(self, account_id=None):
        """获取账户信息"""
        if not self.connected:
            raise ConnectionError("未连接到长桥API")
            
        try:
            # 如果缓存有效，直接返回缓存
            if self.is_cache_valid("account"):
                return self._account_cache
                
            # 获取账户资金
            balance = self.client.get_account_balance()
            
            # 格式化账户信息
            account_info = {
                "account_id": "longport_account",  # 长桥SDK没有直接提供account_id，使用固定值
                "cash": {
                    "currency": balance[0].currency if balance else "HKD",
                    "total": float(balance[0].total_cash) if balance else 0.0,
                    "available": float(balance[0].available_cash) if balance else 0.0,
                    "frozen": float(balance[0].frozen_cash) if balance else 0.0,
                },
                "margin": {
                    "max_finance_amount": float(balance[0].max_finance_amount) if balance else 0.0,
                    "remaining_finance_amount": float(balance[0].remaining_finance_amount) if balance else 0.0,
                },
                "net_assets": float(balance[0].net_assets) if balance else 0.0,
            }
            
            # 更新缓存
            self._account_cache = account_info
            self.refresh_cache("account")
            
            return account_info
        except AttributeError as e:
            self.logger.error(f"获取账户信息失败(属性错误): {str(e)}")
            # 尝试使用不同的属性名
            try:
                # 直接打印balance对象的属性，以便调试
                if balance:
                    self.logger.info(f"AccountBalance属性: {dir(balance[0])}")
                
                # 根据实际存在的属性构建账户信息
                available_cash_attr = "cash_available" if hasattr(balance[0], "cash_available") else "available_balance"
                total_cash_attr = "total_cash" if hasattr(balance[0], "total_cash") else "cash_balance"
                frozen_cash_attr = "frozen_cash" if hasattr(balance[0], "frozen_cash") else "cash_reserved"
                
                account_info = {
                    "account_id": "longport_account",
                    "cash": {
                        "currency": balance[0].currency if balance else "HKD",
                        "total": float(getattr(balance[0], total_cash_attr, 0)),
                        "available": float(getattr(balance[0], available_cash_attr, 0)),
                        "frozen": float(getattr(balance[0], frozen_cash_attr, 0)),
                    },
                    "margin": {
                        "max_finance_amount": float(getattr(balance[0], "max_finance_amount", 0)),
                        "remaining_finance_amount": float(getattr(balance[0], "remaining_finance_amount", 0)),
                    },
                    "net_assets": float(getattr(balance[0], "net_assets", 0)),
                }
                
                # 更新缓存
                self._account_cache = account_info
                self.refresh_cache("account")
                
                return account_info
            except Exception as e2:
                self.logger.error(f"尝试替代属性名仍然失败: {str(e2)}")
                # 返回一个基本的账户信息
                return {
                    "account_id": "longport_account",
                    "cash": {
                        "currency": "HKD",
                        "total": 0.0,
                        "available": 0.0,
                        "frozen": 0.0,
                    },
                    "margin": {
                        "max_finance_amount": 0.0,
                        "remaining_finance_amount": 0.0,
                    },
                    "net_assets": 0.0,
                }
        except Exception as e:
            self.logger.error(f"获取账户信息失败: {str(e)}")
            # 如果有缓存，返回缓存数据
            if self._account_cache:
                self.logger.warning("返回缓存的账户信息")
                return self._account_cache
            raise
            
    def get_positions(self, account_id=None):
        """获取持仓信息"""
        if not self.connected:
            raise ConnectionError("未连接到长桥API")
            
        try:
            # 如果缓存有效，直接返回缓存
            if self.is_cache_valid("positions"):
                return self._positions_cache
                
            # 获取持仓
            positions = self.client.get_positions()
            
            # 格式化持仓信息
            positions_list = []
            for pos in positions:
                positions_list.append({
                    "symbol": pos.symbol,
                    "name": pos.symbol_name,
                    "quantity": int(pos.quantity),
                    "available_quantity": int(pos.available_quantity),
                    "frozen_quantity": int(pos.quantity) - int(pos.available_quantity),
                    "avg_price": float(pos.avg_price),
                    "market_value": float(pos.market_value),
                    "cost_value": float(pos.cost_value),
                    "unrealized_pl": float(pos.unrealized_pl),
                    "unrealized_pl_ratio": float(pos.pl_ratio) if hasattr(pos, 'pl_ratio') else 0.0,
                    "currency": pos.currency,
                    "updated_time": datetime.datetime.now().isoformat(),
                })
            
            # 更新缓存
            self._positions_cache = positions_list
            self.refresh_cache("positions")
            
            return positions_list
        except AttributeError as e:
            self.logger.error(f"获取持仓信息失败(属性错误): {str(e)}")
            # 尝试使用不同的属性名
            try:
                # 如果有持仓，打印第一个持仓的属性以便调试
                if positions and len(positions) > 0:
                    self.logger.info(f"Position对象属性: {dir(positions[0])}")
                
                # 返回空列表，表示没有持仓
                # 实际项目中可以根据打印的属性信息修改代码
                positions_list = []
                
                # 更新缓存
                self._positions_cache = positions_list
                self.refresh_cache("positions")
                
                return positions_list
            except Exception as e2:
                self.logger.error(f"尝试替代属性名仍然失败: {str(e2)}")
                return []
        except Exception as e:
            self.logger.error(f"获取持仓信息失败: {str(e)}")
            # 如果有缓存，返回缓存数据
            if self._positions_cache:
                self.logger.warning("返回缓存的持仓信息")
                return self._positions_cache
            return []
            
    def get_orders(self, account_id=None, status=None):
        """获取订单信息"""
        if not self.connected:
            raise ConnectionError("未连接到长桥API")
            
        try:
            # 强制刷新缓存，获取最新订单
            self._last_refresh["orders"] = datetime.datetime.now() - datetime.timedelta(minutes=10)
                
            # 获取今日订单
            orders = self.client.get_today_orders()
            self.logger.info(f"获取到 {len(orders)} 个订单")
            
            # 如果有订单，打印第一个订单的属性以便调试
            if orders and len(orders) > 0:
                self.logger.info(f"Order对象属性: {dir(orders[0])}")
            
            # 格式化订单信息
            orders_list = []
            for order in orders:
                try:
                    # 由于测试发现executed_price和其他几个属性可能是None，所以需要更安全的处理
                    order_info = {
                        "order_id": order.order_id,
                        "symbol": order.symbol,
                        "name": getattr(order, 'stock_name', ''),
                        "side": str(order.side),
                        "order_type": str(order.order_type),
                        "status": str(order.status),
                        "submitted_quantity": int(order.quantity),
                        "executed_quantity": int(getattr(order, 'executed_quantity', 0)),
                        # 使用price字段替代submitted_price
                        "price": float(order.price) if order.price is not None else 0.0,
                        # 安全处理None值
                        "executed_price": float(order.executed_price) if order.executed_price is not None else 0.0,
                        "submitted_time": order.submitted_at.isoformat() if hasattr(order, 'submitted_at') else '',
                        "updated_time": order.updated_at.isoformat() if hasattr(order, 'updated_at') else '',
                        "currency": order.currency if hasattr(order, 'currency') else 'HKD',
                        "remark": getattr(order, 'remark', ''),
                        "expire_date": order.expire_date.isoformat() if hasattr(order, 'expire_date') and order.expire_date else '',
                    }
                    orders_list.append(order_info)
                except Exception as e:
                    self.logger.error(f"处理订单时出错: {str(e)}")
                    self.logger.error(f"问题订单: {order}")
            
            # 更新缓存
            self._orders_cache = orders_list
            self.refresh_cache("orders")
            
            return orders_list
        except AttributeError as e:
            self.logger.error(f"获取订单信息失败(属性错误): {str(e)}")
            # 尝试使用不同的属性名
            try:
                # 如果有订单，打印第一个订单的属性以便调试
                if orders and len(orders) > 0:
                    self.logger.info(f"Order对象属性: {dir(orders[0])}")
                
                # 根据实际情况重新构建订单列表
                orders_list = []
                
                # 更新缓存
                self._orders_cache = orders_list
                self.refresh_cache("orders")
                
                return orders_list
            except Exception as e2:
                self.logger.error(f"尝试替代属性名仍然失败: {str(e2)}")
                return []
        except Exception as e:
            self.logger.error(f"获取订单信息失败: {str(e)}")
            # 如果有缓存，返回缓存数据
            if self._orders_cache:
                self.logger.warning("返回缓存的订单信息")
                return self._orders_cache
            return []
            
    def place_order(self, order_params):
        """下单"""
        if not self.connected:
            raise ConnectionError("未连接到长桥API")
            
        try:
            # 检查订单参数
            required_params = ["symbol", "order_type", "side", "quantity"]
            for param in required_params:
                if param not in order_params:
                    raise ValueError(f"订单缺少必要参数: {param}")
                    
            # 参数映射和转换
            symbol = order_params["symbol"]
            quantity = int(order_params["quantity"])
            
            # 订单类型映射
            order_type_str = order_params["order_type"].upper()
            if order_type_str == "LIMIT":
                order_type = self.OrderType.LO
                if "price" not in order_params:
                    raise ValueError("限价单必须指定价格")
                submitted_price = float(order_params["price"])
            elif order_type_str == "MARKET":
                order_type = self.OrderType.MO
                submitted_price = None
            else:
                raise ValueError(f"不支持的订单类型: {order_type_str}")
                
            # 买卖方向映射
            side_str = order_params["side"].upper()
            if side_str == "BUY":
                side = self.OrderSide.Buy
            elif side_str == "SELL":
                side = self.OrderSide.Sell
            else:
                raise ValueError(f"不支持的交易方向: {side_str}")
                
            # 有效期设置
            time_in_force = self.TimeInForceType.Day
                
            # 下单
            result = self.client.create_order(
                symbol=symbol,
                order_type=order_type,
                side=side,
                quantity=quantity,
                time_in_force=time_in_force,
                submitted_price=submitted_price if "price" in order_params else None,
                remark=order_params.get("remark", "")
            )
            
            # 清除缓存，强制下次查询刷新
            self._last_refresh["orders"] = datetime.datetime.now() - datetime.timedelta(minutes=10)
            
            return {
                "success": True,
                "order_id": result.order_id,
                "message": "订单提交成功"
            }
        except Exception as e:
            self.logger.error(f"下单失败: {str(e)}")
            return {
                "success": False,
                "message": f"下单失败: {str(e)}"
            }
            
    def cancel_order(self, order_id):
        """撤单"""
        if not self.connected:
            raise ConnectionError("未连接到长桥API")
            
        try:
            # 撤单
            result = self.client.cancel_order(order_id=order_id)
            
            # 清除缓存，强制下次查询刷新
            self._last_refresh["orders"] = datetime.datetime.now() - datetime.timedelta(minutes=10)
            
            return {
                "success": True,
                "order_id": order_id,
                "message": "撤单请求已提交"
            }
        except Exception as e:
            self.logger.error(f"撤单失败: {str(e)}")
            return {
                "success": False,
                "order_id": order_id,
                "message": f"撤单失败: {str(e)}"
            }
            
    def get_market_data(self, symbols, data_type="quote"):
        """获取市场数据"""
        if not self.connected:
            raise ConnectionError("未连接到长桥API")
            
        try:
            if not isinstance(symbols, list):
                symbols = [symbols]
                
            if data_type == "quote":
                # 获取行情
                quotes = self.client.get_quote(symbols=symbols)
                
                # 格式化行情数据
                quote_data = []
                for quote in quotes:
                    quote_data.append({
                        "symbol": quote.symbol,
                        "last_price": float(quote.last_done),
                        "open_price": float(quote.open),
                        "high_price": float(quote.high),
                        "low_price": float(quote.low),
                        "volume": int(quote.volume),
                        "turnover": float(quote.turnover),
                        "timestamp": quote.timestamp.isoformat() if hasattr(quote, 'timestamp') else datetime.datetime.now().isoformat(),
                    })
                return quote_data
            elif data_type == "depth":
                # 获取深度
                depth = self.client.get_depth(symbol=symbols[0])
                
                # 格式化深度数据
                bids = [{"price": float(bid.price), "volume": int(bid.volume)} for bid in depth.bids]
                asks = [{"price": float(ask.price), "volume": int(ask.volume)} for ask in depth.asks]
                
                return {
                    "symbol": symbols[0],
                    "bids": bids,
                    "asks": asks,
                    "timestamp": datetime.datetime.now().isoformat(),
                }
            elif data_type == "candles":
                # 获取K线数据，默认获取日K，100条
                period = order_params.get("period", self.Period.Day)
                count = int(order_params.get("count", 100))
                
                candles = self.client.get_candlesticks(
                    symbol=symbols[0],
                    period=period,
                    count=count,
                    adjust_type=self.AdjustType.NoAdjust
                )
                
                # 格式化K线数据
                candle_data = []
                for candle in candles:
                    candle_data.append({
                        "symbol": symbols[0],
                        "open": float(candle.open),
                        "high": float(candle.high),
                        "low": float(candle.low),
                        "close": float(candle.close),
                        "volume": int(candle.volume),
                        "turnover": float(candle.turnover),
                        "timestamp": candle.timestamp.isoformat(),
                    })
                return candle_data
            else:
                raise ValueError(f"不支持的数据类型: {data_type}")
        except Exception as e:
            self.logger.error(f"获取市场数据失败: {str(e)}")
            raise
            
    def subscribe_quote(self, symbols, callback=None):
        """订阅行情"""
        if not self.connected:
            raise ConnectionError("未连接到长桥API")
            
        try:
            if not isinstance(symbols, list):
                symbols = [symbols]
                
            # 注册回调
            if callback:
                for symbol in symbols:
                    self.client.register_quote_callback(symbol, callback)
                    
            # 订阅行情
            result = self.client.subscribe_quotes(symbols=symbols)
            return {
                "success": True,
                "message": f"成功订阅行情: {symbols}"
            }
        except Exception as e:
            self.logger.error(f"订阅行情失败: {str(e)}")
            return {
                "success": False,
                "message": f"订阅行情失败: {str(e)}"
            }
            
    def unsubscribe_quote(self, symbols):
        """取消订阅行情"""
        if not self.connected:
            raise ConnectionError("未连接到长桥API")
            
        try:
            if not isinstance(symbols, list):
                symbols = [symbols]
                
            # 取消订阅
            result = self.client.unsubscribe_quotes(symbols=symbols)
            return {
                "success": True,
                "message": f"成功取消订阅行情: {symbols}"
            }
        except Exception as e:
            self.logger.error(f"取消订阅行情失败: {str(e)}")
            return {
                "success": False,
                "message": f"取消订阅行情失败: {str(e)}"
            }

# ======== 交易服务器实现 ========

class TradingServer:
    """交易服务器"""
    
    def __init__(self, config=None):
        """初始化交易服务器"""
        self.logger = logging.getLogger("TradingServer")
        self.config = config or {}
        self.interfaces = {}
        self.active_interface = None
        self.strategies = {}
        self.risk_manager = None
        self.signal_processor = None
        self.market_data_cache = {}
        self.heartbeat_time = datetime.datetime.now()
        self.trading_enabled = True
        self.active_strategies = set()
        self.stats = {
            "signals_processed": 0,
            "signals_rejected": 0,
            "orders_today": 0,
        }
        self.risk_limits = {
            "max_position_value": float(os.environ.get("RISK_MAX_POSITION_VALUE", 1000000)),
            "max_daily_orders": int(os.environ.get("RISK_MAX_DAILY_ORDERS", 100)),
            "max_daily_turnover": float(os.environ.get("RISK_MAX_DAILY_TURNOVER", 500000)),
            "max_order_value": float(os.environ.get("RISK_MAX_ORDER_VALUE", 100000)),
            "max_drawdown": float(os.environ.get("RISK_MAX_DRAWDOWN", 0.1)),
        }
        
        # 初始化交易接口
        self._init_trading_interfaces()
    
    def _init_trading_interfaces(self):
        """初始化交易接口"""
        # 优先使用LongPort接口
        try:
            longport_interface = LongPortInterface()
            self.interfaces[longport_interface.name] = longport_interface
            self.active_interface = longport_interface.name
            self.logger.info(f"已加载LongPort交易接口")
        except Exception as e:
            self.logger.warning(f"加载LongPort交易接口失败: {str(e)}")
            
            # 如果LongPort接口不可用，使用模拟接口
            try:
                mock_interface = MockTradingInterface(initial_balance=1000000.0)
                self.interfaces["mock"] = mock_interface
                self.active_interface = "mock"
                self.logger.info(f"已加载模拟交易接口")
            except Exception as e:
                self.logger.error(f"加载模拟交易接口失败: {str(e)}")

        if not self.active_interface:
            self.logger.error("没有可用的交易接口")
            raise ValueError("没有可用的交易接口")
            
        self.logger.info(f"当前激活的交易接口: {self.active_interface}")

    def register_strategy(self, strategy_request):
        """
        注册交易策略
        
        Args:
            strategy_request (StrategyRequest): 策略请求
            
        Returns:
            dict: 注册结果
        """
        strategy_id = strategy_request.strategy_id
        
        # 检查策略ID是否已存在
        if strategy_id in self.strategies:
            return {
                "status": "error",
                "message": f"策略ID {strategy_id} 已存在",
                "strategy_id": strategy_id
            }
        
        # 创建策略配置
        strategy_config = {
            "name": strategy_request.name,
            "description": strategy_request.description,
            "symbols": strategy_request.symbols,
            "interval": strategy_request.interval,
            "parameters": strategy_request.parameters,
            "status": "inactive",
            "created_at": datetime.datetime.now().isoformat(),
            "last_run": None,
            "stats": {
                "signals_generated": 0,
                "orders_created": 0,
                "profit_loss": 0.0
            }
        }
        
        # 注册策略
        self.strategies[strategy_id] = strategy_config
        
        return {
            "status": "success",
            "message": f"策略 {strategy_id} 注册成功",
            "strategy_id": strategy_id,
            "strategy": strategy_config
        }
        
    def start_strategy(self, strategy_id):
        """
        启动策略
        
        Args:
            strategy_id (str): 策略ID
            
        Returns:
            dict: 启动结果
        """
        if strategy_id not in self.strategies:
            return {
                "status": "error",
                "message": f"策略 {strategy_id} 不存在"
            }
            
        if strategy_id in self.active_strategies:
            return {
                "status": "warning",
                "message": f"策略 {strategy_id} 已经处于运行状态"
            }
            
        # 激活策略
        self.active_strategies.add(strategy_id)
        self.strategies[strategy_id]["status"] = "active"
        self.strategies[strategy_id]["last_run"] = datetime.datetime.now().isoformat()
        
        return {
            "status": "success",
            "message": f"策略 {strategy_id} 已启动",
            "strategy_id": strategy_id
        }
        
    def stop_strategy(self, strategy_id):
        """
        停止策略
        
        Args:
            strategy_id (str): 策略ID
            
        Returns:
            dict: 停止结果
        """
        if strategy_id not in self.strategies:
            return {
                "status": "error",
                "message": f"策略 {strategy_id} 不存在"
            }
            
        if strategy_id not in self.active_strategies:
            return {
                "status": "warning",
                "message": f"策略 {strategy_id} 已经处于停止状态"
            }
            
        # 停止策略
        self.active_strategies.remove(strategy_id)
        self.strategies[strategy_id]["status"] = "inactive"
        
        return {
            "status": "success",
            "message": f"策略 {strategy_id} 已停止",
            "strategy_id": strategy_id
        }

    def update_risk_limits(self, limits_request):
        """
        更新风险限制
        
        Args:
            limits_request (RiskLimitsRequest): 风险限制请求
            
        Returns:
            dict: 更新结果
        """
        # 更新风险限制
        if limits_request.max_position_value is not None:
            self.risk_limits["max_position_value"] = limits_request.max_position_value
            
        if limits_request.max_daily_orders is not None:
            self.risk_limits["max_daily_orders"] = limits_request.max_daily_orders
            
        if limits_request.max_daily_turnover is not None:
            self.risk_limits["max_daily_turnover"] = limits_request.max_daily_turnover
            
        if limits_request.max_order_value is not None:
            self.risk_limits["max_order_value"] = limits_request.max_order_value
            
        if limits_request.max_drawdown is not None:
            self.risk_limits["max_drawdown"] = limits_request.max_drawdown
            
        return {
            "status": "success",
            "message": "风险限制已更新",
            "risk_limits": self.risk_limits
        }
        
    def process_signal(self, signal):
        """
        处理交易信号
        
        Args:
            signal (SignalRequest): 交易信号请求
            
        Returns:
            dict: 处理结果
        """
        # 检查交易是否启用
        if not self.trading_enabled:
            return {
                "status": "error",
                "message": "交易已禁用，无法处理信号"
            }
            
        # 检查策略是否存在和激活
        strategy_id = signal.strategy_id
        if strategy_id not in self.strategies:
            return {
                "status": "error",
                "message": f"策略 {strategy_id} 不存在"
            }
            
        if strategy_id not in self.active_strategies:
            return {
                "status": "warning",
                "message": f"策略 {strategy_id} 未激活，信号将被忽略"
            }
            
        # 创建订单请求
        order_request = OrderRequest(
            symbol=signal.symbol,
            direction=signal.direction,
            quantity=signal.quantity or 100,  # 默认数量
            price=signal.price,
            order_type="limit" if signal.price else "market",
            account_id=signal.account_id
        )
        
        # 执行下单
        order_result = self.place_order(order_request)
        
        if order_result:
            # 更新统计信息
            self.stats["signals_processed"] += 1
            self.strategies[strategy_id]["stats"]["signals_generated"] += 1
            self.strategies[strategy_id]["stats"]["orders_created"] += 1
            
            return {
                "status": "success",
                "message": f"信号已处理并创建订单 {order_result.get('order_id')}",
                "order": order_result
            }
        else:
            # 更新统计信息
            self.stats["signals_rejected"] += 1
            
            return {
                "status": "error",
                "message": "下单失败，可能是风险控制限制或交易接口问题"
            }
            
    def get_interface(self):
        """获取当前活跃的交易接口"""
        return self.interfaces.get(self.active_interface) or self.interfaces.get("mock")
        
    def place_order(self, order_request):
        """
        下单
        
        Args:
            order_request (OrderRequest): 下单请求
            
        Returns:
            dict: 下单结果
        """
        # 检查交易是否启用
        if not self.trading_enabled:
            logger.warning("交易已禁用，无法下单")
            return None
            
        # 执行风险检查
        # TODO: 实现更全面的风险检查
        
        # 获取接口和账户ID
        interface = self.get_interface()
        account_id = order_request.account_id or getattr(interface, 'default_account_id', None)
        
        if not interface or not account_id:
            logger.error("无法获取有效的交易接口或账户ID")
            return None
            
        # 执行下单
        try:
            order_result = interface.place_order(
                account_id=account_id,
                symbol=order_request.symbol,
                direction=order_request.direction,
                quantity=order_request.quantity,
                price=order_request.price,
                order_type=order_request.order_type
            )
            
            # 更新统计信息
            self.stats["orders_today"] += 1
            
            return order_result
        except Exception as e:
            logger.error(f"下单失败: {e}")
            return None
            
    def cancel_order(self, account_id=None, order_id=None):
        """
        取消订单
        
        Args:
            account_id (str, optional): 账户ID
            order_id (str): 订单ID
            
        Returns:
            dict: 取消结果
        """
        # 检查交易是否启用
        if not self.trading_enabled:
            logger.warning("交易已禁用，无法取消订单")
            return None
            
        # 获取接口和账户ID
        interface = self.get_interface()
        if not account_id:
            account_id = getattr(interface, 'default_account_id', None)
        
        if not interface or not account_id:
            logger.error("无法获取有效的交易接口或账户ID")
            return None
        
        try:
            result = interface.cancel_order(account_id, order_id)
            logger.info(f"成功取消订单 {order_id}")
            return result
        except Exception as e:
            logger.error(f"取消订单失败: {e}")
            return None
            
    def get_account_info(self, account_id=None):
        """
        获取账户信息
        
        Args:
            account_id (str, optional): 账户ID
            
        Returns:
            dict: 账户信息
        """
        # 直接使用活跃的交易接口
        if self.active_interface and self.active_interface in self.interfaces:
            interface = self.interfaces[self.active_interface]
            return interface.get_account_info(account_id)
        else:
            logger.error("无法获取有效的交易接口")
            return None
        
    def get_positions(self, account_id=None):
        """
        获取持仓信息
        
        Args:
            account_id (str, optional): 账户ID
            
        Returns:
            list: 持仓列表
        """
        # 直接使用活跃的交易接口
        if self.active_interface and self.active_interface in self.interfaces:
            interface = self.interfaces[self.active_interface]
            return interface.get_positions(account_id)
        else:
            logger.error("无法获取有效的交易接口")
            return []
        
    def get_orders(self, account_id=None, status=None):
        """
        获取订单信息
        
        Args:
            account_id (str, optional): 账户ID
            status (str, optional): 订单状态
            
        Returns:
            list: 订单列表
        """
        # 直接使用活跃的交易接口
        if self.active_interface and self.active_interface in self.interfaces:
            interface = self.interfaces[self.active_interface]
            return interface.get_orders(account_id, status)
        else:
            logger.error("无法获取有效的交易接口")
            return []
        
    def get_server_status(self):
        """
        获取服务器状态
        
        Returns:
            dict: 服务器状态
        """
        return {
            "status": "running" if self.trading_enabled else "disabled",
            "trading_interface": self.active_interface,
            "active_strategies": list(self.active_strategies),
            "total_strategies": len(self.strategies),
            "stats": self.stats,
            "risk_limits": self.risk_limits
        }
        
    def enable_trading(self):
        """启用交易"""
        self.trading_enabled = True
        logger.info("交易已启用")
        return {"status": "success", "message": "交易已启用"}
        
    def disable_trading(self):
        """禁用交易"""
        self.trading_enabled = False
        logger.info("交易已禁用")
        return {"status": "success", "message": "交易已禁用"}

    def get_market_data(self, symbols):
        """
        获取市场数据
        
        Args:
            symbols (list): 股票代码列表
            
        Returns:
            dict: 市场数据
        """
        interface = self.get_interface()
        
        if not interface:
            logger.error("无法获取有效的交易接口")
            return {}
            
        try:
            return interface.get_market_data(symbols)
        except Exception as e:
            logger.error(f"获取市场数据失败: {e}")
            return {}

# ======== API服务器实现 ========

class TradingAPIServer:
    """交易API服务器"""
    
    def __init__(self, trading_server, port=8000):
        """初始化API服务器"""
        self.trading_server = trading_server
        self.port = port
        
        if not FASTAPI_AVAILABLE:
            raise ImportError("未安装FastAPI，无法启动API服务")
        
        self.app = fastapi.FastAPI(
            title="量化交易执行服务API",
            description="提供交易执行、账户管理、订单管理等API接口",
            version="1.0.0"
        )
        
        # 注册路由
        self._register_routes()
        
        logger.info(f"API服务器初始化，端口: {self.port}")
    
    def _register_routes(self):
        """注册API路由"""
        @self.app.get("/")
        async def root():
            return {"message": "量化交易执行服务API", "status": "running"}
        
        @self.app.get("/api/heartbeat")
        async def heartbeat():
            return {
                "status": "alive",
                "timestamp": datetime.datetime.now().isoformat(),
                "version": "1.0.0"
            }
        
        @self.app.get("/api/status")
        async def get_status():
            return self.trading_server.get_server_status()
        
        @self.app.get("/api/account")
        async def get_account(account_id: str = None):
            return self.trading_server.get_account_info(account_id)
        
        @self.app.get("/api/positions")
        async def get_positions(account_id: str = None):
            return self.trading_server.get_positions(account_id)
        
        @self.app.get("/api/orders")
        async def get_orders(account_id: str = None, status: str = None):
            return self.trading_server.get_orders(account_id, status)
            
        @self.app.post("/api/orders")
        async def place_order(symbol: str, side: str, order_type: str, quantity: int, price: float = None):
            """下单API"""
            interface = self.trading_server.interfaces.get(self.trading_server.active_interface)
            if not interface:
                return {"success": False, "message": "无法获取交易接口"}
                
            order_params = {
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "quantity": quantity
            }
            
            if price is not None:
                order_params["price"] = price
                
            result = interface.place_order(order_params)
            return result
            
        @self.app.delete("/api/orders/{order_id}")
        async def cancel_order(order_id: str):
            """撤单API"""
            interface = self.trading_server.interfaces.get(self.trading_server.active_interface)
            if not interface:
                return {"success": False, "message": "无法获取交易接口"}
                
            result = interface.cancel_order(order_id)
            return result


# ======== 主程序 ========

def create_longport_env_example():
    """创建长桥SDK环境变量示例文件"""
    example_env = """# 长桥SDK环境变量配置示例
# 将此文件复制为.env并填入您的实际值

# 长桥SDK认证信息
LB_APP_KEY=your_app_key_here
LB_APP_SECRET=your_app_secret_here
LB_ACCESS_TOKEN=your_access_token_here

# 交易服务器配置
TRADING_SERVER_PORT=8000
TRADING_DEBUG_MODE=true

# 风险控制参数
RISK_MAX_POSITION_VALUE=1000000
RISK_MAX_DAILY_ORDERS=100
RISK_MAX_DAILY_TURNOVER=500000
RISK_MAX_ORDER_VALUE=100000
RISK_MAX_DRAWDOWN=0.1

# 日志配置
LOG_LEVEL=INFO
LOG_FILE=trading_server.log
"""
    # 写入示例文件
    with open("longport_env_example.txt", "w") as f:
        f.write(example_env)
    return "已创建长桥SDK环境变量示例文件: longport_env_example.txt"

def cli_main():
    """命令行接口入口函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="量化交易执行服务")
    parser.add_argument("--port", type=int, default=8000, help="API服务器端口")
    parser.add_argument("--debug", action="store_true", help="启用调试模式")
    parser.add_argument("--interface", type=str, default="longport", help="使用的交易接口 (longport)")
    parser.add_argument("--create-env", action="store_true", help="创建环境变量示例文件")
    
    args = parser.parse_args()
    
    # 设置日志级别
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 创建环境变量示例文件
    if args.create_env:
        print(create_longport_env_example())
        return
    
    # 启动交易服务器
    try:
        print(f"启动交易服务器，接口: {args.interface}，端口: {args.port}，调试模式: {'开启' if args.debug else '关闭'}")
        server = TradingServer(config={
            'debug': args.debug,
            'port': args.port,
            'interface': args.interface
        })
        
        if FASTAPI_AVAILABLE:
            api_server = TradingAPIServer(server, port=args.port)
            import uvicorn
            uvicorn.run(api_server.app, host="0.0.0.0", port=args.port)
        else:
            print("未安装FastAPI，仅启动交易服务器，无API服务")
            print("账户信息:", server.get_account_info())
            # 保持程序运行
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("交易服务器已停止")
    except Exception as e:
        print(f"启动交易服务器失败: {e}")

if __name__ == "__main__":
    cli_main() 