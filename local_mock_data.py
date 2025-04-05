#!/usr/bin/env python3
"""
长桥API本地模拟数据生成器
提供模拟的股票行情、交易会话和账户数据，用于算法开发和测试
"""

import datetime
import random
import json
import time
from typing import Dict, List, Any, Optional, Union


class LongPortAPIMock:
    def __init__(self):
        # 初始化模拟数据
        self.stocks = {
            "700.HK": {"name": "腾讯控股", "lot_size": 100, "industry": "科技"},
            "9988.HK": {"name": "阿里巴巴", "lot_size": 100, "industry": "科技"},
            "0700.HK": {"name": "腾讯控股", "lot_size": 100, "industry": "科技"},
            "AAPL.US": {"name": "苹果公司", "lot_size": 1, "industry": "科技"},
            "MSFT.US": {"name": "微软", "lot_size": 1, "industry": "科技"},
            "GOOGL.US": {"name": "谷歌", "lot_size": 1, "industry": "科技"},
            "AMZN.US": {"name": "亚马逊", "lot_size": 1, "industry": "零售"},
            "TSLA.US": {"name": "特斯拉", "lot_size": 1, "industry": "汽车"},
            "BABA.US": {"name": "阿里巴巴", "lot_size": 1, "industry": "科技"},
            "3690.HK": {"name": "美团", "lot_size": 100, "industry": "消费"},
            "9999.HK": {"name": "网易", "lot_size": 100, "industry": "科技"},
            "1211.HK": {"name": "比亚迪", "lot_size": 500, "industry": "汽车"},
        }
        
        # 模拟账户余额
        self.account_balance = {
            "cash": {
                "currency": "HKD",
                "available_balance": 1000000.0,
                "cash_balance": 1000000.0,
                "buying_power": 4000000.0,
                "settling_balance": 0.0,
                "available_amount": 1000000.0,
            },
            "holdings": [],
        }
        
        # 模拟订单历史
        self.orders = []
        
        # 模拟行情数据
        self.last_prices = {}
        for symbol in self.stocks:
            base_price = random.uniform(100, 1000) if ".HK" in symbol else random.uniform(50, 500)
            self.last_prices[symbol] = round(base_price, 2)
        
        # 交易市场状态
        self.market_status = {
            "HK": {
                "market_status": "TRADING",
                "time_zone": "Asia/Hong_Kong",
                "trade_session": [
                    {"begin_time": "09:30:00", "end_time": "12:00:00", "trade_session": "MORNING"}, 
                    {"begin_time": "13:00:00", "end_time": "16:00:00", "trade_session": "AFTERNOON"}
                ]
            },
            "US": {
                "market_status": "TRADING" if 9 <= datetime.datetime.now().hour <= 16 else "CLOSED",
                "time_zone": "America/New_York",
                "trade_session": [
                    {"begin_time": "09:30:00", "end_time": "16:00:00", "trade_session": "REGULAR"}
                ]
            }
        }
        
    def get_market_status(self, market: str) -> Dict:
        """获取市场状态"""
        if market.upper() in self.market_status:
            return self.market_status[market.upper()]
        return {"error": "Market not found"}
    
    def get_stock_quote(self, symbol: str) -> Dict:
        """获取股票实时报价"""
        if symbol not in self.stocks:
            return {"error": "Stock not found"}
        
        # 随机生成价格波动 (-1% ~ +1%)
        price_change = self.last_prices[symbol] * random.uniform(-0.01, 0.01)
        current_price = round(self.last_prices[symbol] + price_change, 2)
        self.last_prices[symbol] = current_price
        
        return {
            "symbol": symbol,
            "name": self.stocks[symbol]["name"],
            "last_done": current_price,
            "open": round(current_price * random.uniform(0.98, 1.02), 2),
            "high": round(current_price * random.uniform(1.0, 1.05), 2),
            "low": round(current_price * random.uniform(0.95, 1.0), 2),
            "timestamp": int(time.time()),
            "volume": random.randint(10000, 1000000),
            "turnover": random.randint(1000000, 100000000),
            "lot_size": self.stocks[symbol]["lot_size"]
        }
    
    def get_account_balance(self) -> Dict:
        """获取账户余额"""
        return self.account_balance
    
    def place_order(self, symbol: str, quantity: int, side: str, order_type: str, price: Optional[float] = None) -> Dict:
        """
        下单接口
        :param symbol: 股票代码
        :param quantity: 数量
        :param side: BUY/SELL
        :param order_type: MARKET/LIMIT
        :param price: 限价单价格
        :return: 订单信息
        """
        if symbol not in self.stocks:
            return {"error": "Stock not found"}
        
        # 检查数量是否符合每手股数
        lot_size = self.stocks[symbol]["lot_size"]
        if quantity % lot_size != 0:
            return {"error": f"Quantity must be a multiple of lot size {lot_size}"}
        
        # 市价单自动生成合理价格
        if order_type == "MARKET":
            price = self.last_prices[symbol]
        elif price is None:
            return {"error": "Price must be specified for LIMIT orders"}
        
        # 生成订单ID
        order_id = f"O{int(time.time())}{random.randint(1000, 9999)}"
        
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "quantity": quantity,
            "side": side,
            "order_type": order_type,
            "price": price,
            "status": "SUBMITTED",
            "create_time": int(time.time()),
            "update_time": int(time.time())
        }
        
        self.orders.append(order)
        
        # 模拟订单处理
        if random.random() > 0.1:  # 90%概率成功
            order["status"] = "FILLED"
            
            # 更新账户余额
            order_value = quantity * price
            if side == "BUY":
                self.account_balance["cash"]["available_balance"] -= order_value
                self.account_balance["cash"]["cash_balance"] -= order_value
                
                # 添加持仓
                holding_exists = False
                for holding in self.account_balance["holdings"]:
                    if holding["symbol"] == symbol:
                        holding["quantity"] += quantity
                        holding["avg_price"] = (holding["avg_price"] * holding["quantity"] + price * quantity) / (holding["quantity"] + quantity)
                        holding_exists = True
                        break
                
                if not holding_exists:
                    self.account_balance["holdings"].append({
                        "symbol": symbol,
                        "name": self.stocks[symbol]["name"],
                        "quantity": quantity,
                        "avg_price": price,
                        "current_price": price
                    })
            else:  # SELL
                self.account_balance["cash"]["available_balance"] += order_value
                self.account_balance["cash"]["cash_balance"] += order_value
                
                # 更新持仓
                for i, holding in enumerate(self.account_balance["holdings"]):
                    if holding["symbol"] == symbol:
                        if holding["quantity"] <= quantity:
                            # 全部卖出
                            self.account_balance["holdings"].pop(i)
                        else:
                            # 部分卖出
                            holding["quantity"] -= quantity
                        break
        else:
            # 随机失败
            order["status"] = "REJECTED"
            order["reject_reason"] = "Insufficient liquidity"
        
        return order
    
    def get_orders(self, status: Optional[str] = None) -> List[Dict]:
        """获取订单列表"""
        if status:
            return [order for order in self.orders if order["status"] == status]
        return self.orders
    
    def cancel_order(self, order_id: str) -> Dict:
        """撤销订单"""
        for order in self.orders:
            if order["order_id"] == order_id and order["status"] == "SUBMITTED":
                order["status"] = "CANCELED"
                order["update_time"] = int(time.time())
                return {"success": True, "order_id": order_id}
        
        return {"success": False, "error": "Order not found or cannot be canceled"}
    
    def get_candlesticks(self, symbol: str, period: str = "1d", count: int = 10) -> List[Dict]:
        """获取K线数据"""
        if symbol not in self.stocks:
            return {"error": "Stock not found"}
        
        current_price = self.last_prices[symbol]
        candles = []
        
        for i in range(count):
            # 生成合理的历史数据
            close_price = round(current_price * random.uniform(0.9, 1.1), 2)
            open_price = round(close_price * random.uniform(0.98, 1.02), 2)
            high_price = round(max(close_price, open_price) * random.uniform(1.0, 1.05), 2)
            low_price = round(min(close_price, open_price) * random.uniform(0.95, 1.0), 2)
            
            # 计算时间戳
            if period == "1d":
                timestamp = int(time.time()) - i * 86400
            elif period == "1h":
                timestamp = int(time.time()) - i * 3600
            else:
                timestamp = int(time.time()) - i * 60
            
            candles.append({
                "symbol": symbol,
                "timestamp": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": random.randint(10000, 1000000),
                "turnover": random.randint(1000000, 100000000)
            })
        
        candles.reverse()  # 按时间正序排列
        return candles


# 使用示例
if __name__ == "__main__":
    api = LongPortAPIMock()
    
    # 获取市场状态
    hk_status = api.get_market_status("HK")
    print("香港市场状态:", json.dumps(hk_status, indent=2))
    
    # 获取股票报价
    tencent_quote = api.get_stock_quote("700.HK")
    print("\n腾讯控股报价:", json.dumps(tencent_quote, indent=2))
    
    # 获取账户余额
    balance = api.get_account_balance()
    print("\n账户余额:", json.dumps(balance, indent=2))
    
    # 下单
    order = api.place_order("700.HK", 100, "BUY", "LIMIT", 400.0)
    print("\n下单结果:", json.dumps(order, indent=2))
    
    # 获取K线数据
    candles = api.get_candlesticks("700.HK", "1d", 5)
    print("\nK线数据:", json.dumps(candles, indent=2)) 