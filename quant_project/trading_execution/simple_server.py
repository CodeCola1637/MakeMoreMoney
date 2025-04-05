"""
简化版交易服务器实现

提供基本的交易接口功能，用于测试和简单场景
"""
import logging
import uuid
import random
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("simple_server")

class MockTradingInterface:
    """简化版模拟交易接口"""
    
    def __init__(self, initial_balance: float = 1000000.0):
        """初始化模拟交易接口"""
        self.account_id = f"mock_{int(datetime.now().timestamp())}"
        self.balance = initial_balance
        self.positions = {}  # 持仓，格式: {symbol: {quantity, avg_price, ...}}
        self.orders = {}     # 订单，格式: {order_id: {symbol, direction, ...}}
        self.market_data = {}  # 市场数据，格式: {symbol: {price, volume, ...}}
        
        # 初始化一些示例股票价格
        self._init_market_data()
        
        logger.info(f"模拟交易接口初始化，账户ID: {self.account_id}，初始资金: {initial_balance}")
    
    def _init_market_data(self):
        """初始化示例市场数据"""
        sample_stocks = {
            "700.HK": {"name": "腾讯控股", "price": 480.0, "pre_close": 475.0},
            "9988.HK": {"name": "阿里巴巴", "price": 80.0, "pre_close": 81.5},
            "3690.HK": {"name": "美团-W", "price": 110.0, "pre_close": 112.5},
            "AAPL.US": {"name": "苹果公司", "price": 175.0, "pre_close": 173.2},
            "MSFT.US": {"name": "微软", "price": 330.0, "pre_close": 327.5},
        }
        
        for symbol, data in sample_stocks.items():
            self.market_data[symbol] = {
                "symbol": symbol,
                "name": data["name"],
                "price": data["price"],
                "open": data["pre_close"] * (1 + (random.random() - 0.5) * 0.02),
                "high": data["price"] * (1 + random.random() * 0.01),
                "low": data["price"] * (1 - random.random() * 0.01),
                "volume": int(random.random() * 1000000),
                "turnover": int(random.random() * 100000000),
                "pre_close": data["pre_close"],
                "time": datetime.now().isoformat()
            }
    
    def get_account_info(self) -> Dict[str, Any]:
        """获取账户信息"""
        # 计算持仓市值
        total_position_value = 0.0
        for symbol, pos in self.positions.items():
            current_price = self.market_data.get(symbol, {}).get("price", 0)
            total_position_value += pos["quantity"] * current_price
        
        # 计算冻结资金
        frozen_cash = 0.0
        for order_id, order in self.orders.items():
            if order["status"] in ["pending", "partial"]:
                # 买单冻结资金
                if order["direction"] == "buy":
                    unfilled_qty = order["quantity"] - order["filled_quantity"]
                    frozen_cash += unfilled_qty * order["price"]
        
        return {
            "account_id": self.account_id,
            "balance": self.balance,
            "frozen_cash": frozen_cash,
            "available_cash": self.balance - frozen_cash,
            "total_value": self.balance + total_position_value,
            "position_value": total_position_value,
            "currency": "HKD"
        }
    
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取持仓信息"""
        result = []
        
        positions_to_check = {}
        if symbol:
            if symbol in self.positions:
                positions_to_check[symbol] = self.positions[symbol]
        else:
            positions_to_check = self.positions
        
        for sym, pos in positions_to_check.items():
            current_price = self.market_data.get(sym, {}).get("price", 0)
            market_value = pos["quantity"] * current_price
            unrealized_pnl = market_value - (pos["quantity"] * pos["average_cost"])
            
            result.append({
                "symbol": sym,
                "quantity": pos["quantity"],
                "average_cost": pos["average_cost"],
                "current_price": current_price,
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": pos.get("realized_pnl", 0.0),
                "update_time": datetime.now().isoformat()
            })
        
        return result
    
    def get_orders(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取订单信息"""
        result = []
        
        for order_id, order in self.orders.items():
            if status and order["status"] != status:
                continue
            
            result.append(order.copy())
        
        return result
    
    def place_order(self, symbol: str, direction: str, quantity: int, 
                   price: Optional[float] = None, order_type: str = "limit") -> Dict[str, Any]:
        """下单"""
        # 生成订单ID
        order_id = f"order_{uuid.uuid4().hex[:8]}_{int(datetime.now().timestamp())}"
        
        # 如果是市价单且未指定价格，使用当前价格
        if order_type == "market" or price is None:
            price = self.market_data.get(symbol, {}).get("price", 0)
            if price == 0:
                return {"success": False, "error": "无法获取市场价格"}
        
        # 创建订单
        order = {
            "order_id": order_id,
            "account_id": self.account_id,
            "symbol": symbol,
            "direction": direction,
            "price": price,
            "quantity": quantity,
            "order_type": order_type,
            "status": "pending",
            "create_time": datetime.now().isoformat(),
            "update_time": datetime.now().isoformat(),
            "filled_quantity": 0,
            "average_price": None,
            "remark": f"模拟{direction}单"
        }
        
        # 保存订单
        self.orders[order_id] = order
        
        # 立即处理订单，无论是市价单还是限价单（在测试环境中）
        self._process_order(order_id)
        
        logger.info(f"创建订单: {order_id}, {symbol} {direction} {quantity}股 @ {price}")
        
        return {
            "success": True,
            "order_id": order_id,
            "status": order["status"]
        }
    
    def cancel_order(self, order_id: str) -> Dict[str, bool]:
        """取消订单"""
        if order_id not in self.orders:
            return {"success": False, "error": "订单不存在"}
        
        order = self.orders[order_id]
        if order["status"] in ["filled", "canceled", "rejected"]:
            return {"success": False, "error": f"订单状态为 {order['status']}，无法取消"}
        
        # 取消订单
        order["status"] = "canceled"
        order["update_time"] = datetime.now().isoformat()
        
        logger.info(f"取消订单: {order_id}")
        
        return {"success": True}
    
    def get_market_data(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """获取市场数据"""
        result = {}
        
        # 随机更新价格
        self._update_market_data()
        
        for symbol in symbols:
            if symbol in self.market_data:
                result[symbol] = self.market_data[symbol]
        
        return result
    
    def get_history_data(self, symbol: str, period: str = "day", 
                        count: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取历史数据"""
        # 生成模拟的历史数据
        result = []
        current_price = self.market_data.get(symbol, {}).get("price", 100.0)
        count = count or 30
        
        for i in range(count):
            day_offset = count - i - 1
            price_change = (random.random() - 0.5) * 0.05  # 每天价格变动幅度在 ±2.5%
            
            if i > 0:
                base_price = result[i-1]["close"]
            else:
                base_price = current_price * (1 - random.random() * 0.1)  # 初始价格
            
            open_price = base_price * (1 + (random.random() - 0.5) * 0.01)
            close_price = base_price * (1 + price_change)
            high_price = max(open_price, close_price) * (1 + random.random() * 0.01)
            low_price = min(open_price, close_price) * (1 - random.random() * 0.01)
            
            bar_data = {
                "symbol": symbol,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": int(random.random() * 1000000),
                "turnover": int(random.random() * 100000000),
                "timestamp": (datetime.now().replace(hour=16, minute=0, second=0) \
                              - timedelta(days=day_offset)).isoformat()
            }
            
            result.append(bar_data)
        
        return result
    
    def _update_market_data(self):
        """随机更新市场数据"""
        for symbol, data in self.market_data.items():
            # 随机生成价格变动，在 ±0.5% 范围内
            price_change = (random.random() - 0.5) * 0.01
            new_price = data["price"] * (1 + price_change)
            
            # 更新价格和时间
            data["price"] = round(new_price, 2)
            data["high"] = max(data["high"], data["price"])
            data["low"] = min(data["low"], data["price"])
            data["volume"] += int(random.random() * 10000)
            data["turnover"] += int(data["price"] * random.random() * 10000)
            data["time"] = datetime.now().isoformat()
    
    def _process_order(self, order_id: str):
        """处理订单（模拟成交）"""
        if order_id not in self.orders:
            return
        
        order = self.orders[order_id]
        if order["status"] not in ["pending", "partial"]:
            return
        
        symbol = order["symbol"]
        direction = order["direction"]
        quantity = order["quantity"]
        price = order["price"]
        
        # 获取当前市场价格
        current_price = self.market_data.get(symbol, {}).get("price", 0)
        if current_price == 0:
            return
        
        # 判断是否可以成交
        can_execute = False
        if order["order_type"] == "market":
            can_execute = True
        elif direction == "buy" and price >= current_price:
            can_execute = True
        elif direction == "sell" and price <= current_price:
            can_execute = True
        
        if not can_execute:
            return
        
        # 模拟成交
        unfilled_qty = quantity - order["filled_quantity"]
        
        # 随机部分成交或全部成交
        fill_percent = 1.0  # 默认全部成交
        if random.random() < 0.3:  # 30% 概率部分成交
            fill_percent = random.random() * 0.8 + 0.2  # 成交比例在 20%-100%
        
        fill_qty = int(unfilled_qty * fill_percent)
        if fill_qty <= 0:
            return
        
        execution_price = current_price
        
        # 更新订单
        order["filled_quantity"] += fill_qty
        if order["filled_quantity"] == quantity:
            order["status"] = "filled"
        else:
            order["status"] = "partial"
        
        order["average_price"] = execution_price
        order["update_time"] = datetime.now().isoformat()
        
        # 更新账户和持仓
        if direction == "buy":
            # 买入：扣减资金，增加持仓
            transaction_amount = fill_qty * execution_price
            
            # 检查资金是否足够
            if transaction_amount > self.balance:
                order["status"] = "rejected"
                order["remark"] = "资金不足"
                return
            
            self.balance -= transaction_amount
            
            # 更新持仓
            if symbol not in self.positions:
                self.positions[symbol] = {
                    "quantity": fill_qty,
                    "average_cost": execution_price,
                    "realized_pnl": 0.0
                }
            else:
                # 计算新的平均成本
                current_qty = self.positions[symbol]["quantity"]
                current_cost = self.positions[symbol]["average_cost"]
                
                new_qty = current_qty + fill_qty
                new_cost = (current_qty * current_cost + fill_qty * execution_price) / new_qty
                
                self.positions[symbol]["quantity"] = new_qty
                self.positions[symbol]["average_cost"] = new_cost
        
        elif direction == "sell":
            # 卖出：增加资金，减少持仓
            transaction_amount = fill_qty * execution_price
            self.balance += transaction_amount
            
            # 检查持仓是否足够
            if symbol not in self.positions or self.positions[symbol]["quantity"] < fill_qty:
                order["status"] = "rejected"
                order["remark"] = "持仓不足"
                return
            
            # 计算实现盈亏
            avg_cost = self.positions[symbol]["average_cost"]
            realized_pnl = (execution_price - avg_cost) * fill_qty
            
            # 更新持仓
            self.positions[symbol]["quantity"] -= fill_qty
            self.positions[symbol]["realized_pnl"] = self.positions[symbol].get("realized_pnl", 0.0) + realized_pnl
            
            # 如果持仓为0，可以选择删除该持仓记录
            if self.positions[symbol]["quantity"] == 0:
                del self.positions[symbol]
        
        logger.info(f"订单执行: {order_id}, {symbol} {direction} {fill_qty}股 @ {execution_price}")

class SimpleServer:
    """简化版交易服务器"""
    
    def __init__(self, interface=None):
        """初始化交易服务器"""
        self.interface = interface or MockTradingInterface()
        logger.info("简化版交易服务器初始化完成")
    
    def get_account_info(self) -> Dict[str, Any]:
        """获取账户信息"""
        return self.interface.get_account_info()
    
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取持仓信息"""
        return self.interface.get_positions(symbol)
    
    def get_orders(self, symbol: Optional[str] = None, status: Optional[str] = None, 
                  start_time: Optional[str] = None, end_time: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取订单信息"""
        # 简化版只实现了按状态筛选
        orders = self.interface.get_orders(status)
        
        # 如果指定了symbol，过滤结果
        if symbol:
            orders = [order for order in orders if order["symbol"] == symbol]
        
        return orders
    
    def place_order(self, symbol: str, direction: str, quantity: int, 
                   price: Optional[float] = None, order_type: str = "limit", 
                   strategy_id: Optional[str] = None) -> Dict[str, Any]:
        """下单"""
        result = self.interface.place_order(symbol, direction, quantity, price, order_type)
        
        # 添加策略ID
        if strategy_id and "order_id" in result:
            order_id = result["order_id"]
            if order_id in self.interface.orders:
                self.interface.orders[order_id]["strategy_id"] = strategy_id
        
        return result
    
    def cancel_order(self, order_id: str) -> Dict[str, bool]:
        """取消订单"""
        return self.interface.cancel_order(order_id)
    
    def get_market_data(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """获取市场数据"""
        return self.interface.get_market_data(symbols)
    
    def get_history_data(self, symbol: str, period: str = "day", count: Optional[int] = None,
                        start_time: Optional[str] = None, end_time: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取历史数据"""
        # 简化版只实现了按count获取数据
        return self.interface.get_history_data(symbol, period, count)
    
    def get_strategies(self) -> List[Dict[str, Any]]:
        """获取策略列表"""
        # 简化版返回空列表
        return [] 