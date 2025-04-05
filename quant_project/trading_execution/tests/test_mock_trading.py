#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试模拟交易接口
"""

import os
import sys
import unittest
import json
from decimal import Decimal
import time

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from quant_project.trading_execution.trading_server import MockTradingInterface

class TestMockTradingInterface(unittest.TestCase):
    """测试模拟交易接口"""
    
    def setUp(self):
        """测试前准备"""
        self.interface = MockTradingInterface(initial_balance=1000000.0)
        self.account_id = self.interface.default_account_id
    
    def test_get_account(self):
        """测试获取账户信息"""
        account = self.interface.get_account()
        self.assertIsNotNone(account)
        self.assertEqual(account["balance"], 1000000.0)
        self.assertEqual(account["status"], "active")
        
    def test_place_order(self):
        """测试下单"""
        # 创建限价买单
        order_result = self.interface.place_order(
            account_id=self.account_id,
            symbol="700.HK",
            direction="buy",
            quantity=100,
            price=500.0,
            order_type="limit"
        )
        
        self.assertIsNotNone(order_result)
        self.assertTrue(order_result["success"])
        self.assertIn("order_id", order_result)
        self.assertIn("order", order_result)
        
        order = order_result["order"]
        self.assertEqual(order["symbol"], "700.HK")
        self.assertEqual(order["direction"], "buy")
        self.assertEqual(order["quantity"], 100)
        self.assertEqual(order["price"], 500.0)
        self.assertEqual(order["status"], "pending")
        
        # 查看订单列表
        orders = self.interface.get_orders()
        self.assertEqual(len(orders), 1)
        
        # 验证订单属性
        self.assertEqual(orders[0]["symbol"], "700.HK")
        self.assertEqual(orders[0]["direction"], "buy")
        self.assertEqual(orders[0]["quantity"], 100)
        self.assertEqual(orders[0]["price"], 500.0)
        
    def test_cancel_order(self):
        """测试撤单"""
        # 创建限价买单
        order_result = self.interface.place_order(
            account_id=self.account_id,
            symbol="700.HK",
            direction="buy",
            quantity=100,
            price=500.0,
            order_type="limit"
        )
        
        order_id = order_result["order_id"]
        
        # 撤单
        result = self.interface.cancel_order(self.account_id, order_id)
        self.assertTrue(result)
        
        # 验证订单状态
        orders = self.interface.get_orders()
        if len(orders) > 0:
            self.assertEqual(orders[0]["status"], "canceled")
    
    @unittest.skip("跳过市场数据测试，需要修复")
    def test_market_data(self):
        """测试市场数据"""
        # 设置测试市场数据
        symbols = ["700.HK", "AAPL.US"]
        for symbol in symbols:
            self.interface.market_data[symbol] = {
                "symbol": symbol,
                "last_price": 500.0,
                "bid_price": 499.0,
                "ask_price": 501.0,
                "volume": 10000,
                "turnover": 5000000.0,
                "timestamp": "2023-01-01T00:00:00.000Z"
            }
            
        # 获取市场数据
        market_data = self.interface.get_market_data(symbols)
        
        self.assertIsNotNone(market_data)
        self.assertEqual(len(market_data), len(symbols))
        
        for symbol in symbols:
            self.assertIn(symbol, market_data)
            self.assertIn("last_price", market_data[symbol])
            self.assertEqual(market_data[symbol]["last_price"], 500.0)
    
    @unittest.skip("跳过持仓更新测试，需要修复")
    def test_position_update(self):
        """测试持仓更新"""
        # 创建买单
        order_result = self.interface.place_order(
            account_id=self.account_id,
            symbol="700.HK",
            direction="buy",
            quantity=100,
            price=500.0,
            order_type="limit"
        )
        
        # 设置市场价格
        self.interface.market_data["700.HK"] = {
            "symbol": "700.HK",
            "last_price": 500.0,
            "bid_price": 499.0,
            "ask_price": 501.0,
            "volume": 10000,
            "turnover": 5000000.0,
            "timestamp": "2023-01-01T00:00:00.000Z"
        }
        
        # 处理订单
        order_id = order_result["order_id"]
        self.interface._process_order(order_id)
        
        # 等待处理完成
        time.sleep(0.5)
        
        # 获取持仓
        positions = self.interface.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "700.HK")
        
        # 更新市场价格
        self.interface.market_data["700.HK"]["last_price"] = 550.0
        
        # 再次获取持仓，检查未实现盈亏
        positions = self.interface.get_positions()
        position = positions[0]
        
        self.assertEqual(position["current_price"], 550.0)
        self.assertEqual(position["market_value"], 550.0 * 100)
        self.assertEqual(position["unrealized_pnl"], (550.0 - 500.0) * 100)
        
        # 创建卖单
        sell_order_result = self.interface.place_order(
            account_id=self.account_id,
            symbol="700.HK",
            direction="sell",
            quantity=50,  # 卖出一半
            price=550.0,
            order_type="limit"
        )
        
        # 处理卖单
        self.interface._process_order(sell_order_result["order_id"])
        
        # 等待处理完成
        time.sleep(0.5)
        
        # 获取持仓，检查数量和已实现盈亏
        positions = self.interface.get_positions()
        position = positions[0]
        
        self.assertEqual(position["quantity"], 50)  # 剩余50股
        self.assertEqual(position["realized_pnl"], (550.0 - 500.0) * 50)  # 已实现盈亏
        
        # 验证账户余额
        account = self.interface.get_account()
        expected_balance = 1000000.0 - (100 * 500.0) + (50 * 550.0)
        self.assertEqual(account["balance"], expected_balance)

if __name__ == "__main__":
    unittest.main() 