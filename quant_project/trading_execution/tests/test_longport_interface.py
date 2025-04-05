#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试LongPort交易接口
"""

import os
import sys
import unittest
import json
from unittest.mock import patch, MagicMock
from decimal import Decimal

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from quant_project.trading_execution.trading_server import LongPortInterface

class TestLongPortInterface(unittest.TestCase):
    """测试LongPort交易接口"""
    
    @patch('quant_project.trading_execution.trading_server.LongPortClient')
    def setUp(self, mock_client_class):
        """测试前准备"""
        # 设置模拟的LongPortClient
        self.mock_client = mock_client_class.return_value
        
        # 模拟trade_ctx和quote_ctx
        self.mock_trade_ctx = MagicMock()
        self.mock_quote_ctx = MagicMock()
        
        # 设置模拟属性
        type(self.mock_client).trade_ctx = self.mock_trade_ctx
        type(self.mock_client).quote_ctx = self.mock_quote_ctx
        
        # 创建接口实例
        self.interface = LongPortInterface()
        self.interface.client = self.mock_client
        
        # 模拟获取账户ID
        self.mock_account_id = "test_account"
        self.interface.account_id = self.mock_account_id
    
    def test_get_account(self):
        """测试获取账户信息"""
        # 模拟账户余额返回值
        mock_balance = MagicMock()
        mock_balance.total_cash = Decimal("1000000.0")
        mock_balance.available_cash = Decimal("900000.0")
        mock_balance.frozen_cash = Decimal("100000.0")
        mock_balance.currency = "HKD"
        
        self.mock_trade_ctx.account_balance.return_value = [mock_balance]
        
        # 调用接口获取账户信息
        account = self.interface.get_account()
        
        # 验证结果
        self.assertIsNotNone(account)
        self.assertEqual(account["balance"], 1000000.0)
        self.assertEqual(account["available_balance"], 900000.0)
        self.assertEqual(account["frozen_balance"], 100000.0)
        self.assertEqual(account["currency"], "HKD")
        
        # 验证方法调用
        self.mock_trade_ctx.account_balance.assert_called_once()
    
    def test_get_positions(self):
        """测试获取持仓信息"""
        # 模拟持仓返回值
        mock_position = MagicMock()
        mock_position.symbol = "700.HK"
        mock_position.quantity = 100
        mock_position.cost_price = Decimal("500.0")
        mock_position.market_value = Decimal("55000.0")
        
        self.mock_trade_ctx.stock_positions.return_value = [mock_position]
        
        # 模拟行情返回值
        mock_quote = MagicMock()
        mock_quote.last_done = Decimal("550.0")
        
        self.mock_quote_ctx.quote.return_value = [mock_quote]
        
        # 调用接口获取持仓信息
        positions = self.interface.get_positions()
        
        # 验证结果
        self.assertEqual(len(positions), 1)
        position = positions[0]
        self.assertEqual(position["symbol"], "700.HK")
        self.assertEqual(position["quantity"], 100)
        self.assertEqual(position["average_cost"], 500.0)
        
        # 验证方法调用
        self.mock_trade_ctx.stock_positions.assert_called_once()
    
    def test_get_orders(self):
        """测试获取订单信息"""
        # 模拟订单返回值
        mock_order = MagicMock()
        mock_order.order_id = "test_order_id"
        mock_order.symbol = "700.HK"
        mock_order.side = "Buy"
        mock_order.quantity = 100
        mock_order.executed_quantity = 0
        mock_order.price = Decimal("500.0")
        mock_order.status = "PendingNew"
        mock_order.create_time = "2023-01-01T00:00:00.000Z"
        
        self.mock_trade_ctx.today_orders.return_value = [mock_order]
        
        # 调用接口获取订单信息
        orders = self.interface.get_orders()
        
        # 验证结果
        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(order["order_id"], "test_order_id")
        self.assertEqual(order["symbol"], "700.HK")
        self.assertEqual(order["direction"], "buy")
        self.assertEqual(order["quantity"], 100)
        self.assertEqual(order["filled_quantity"], 0)
        self.assertEqual(order["price"], 500.0)
        
        # 验证方法调用
        self.mock_trade_ctx.today_orders.assert_called_once()
    
    def test_place_order(self):
        """测试下单"""
        # 模拟下单返回值
        mock_order_result = MagicMock()
        mock_order_result.order_id = "test_order_id"
        
        self.mock_trade_ctx.submit_order.return_value = mock_order_result
        
        # 调用接口下单
        order = self.interface.place_order(
            account_id=self.mock_account_id,
            symbol="700.HK",
            direction="buy",
            quantity=100,
            price=500.0,
            order_type="limit"
        )
        
        # 验证结果
        self.assertIsNotNone(order)
        self.assertEqual(order["order_id"], "test_order_id")
        self.assertEqual(order["symbol"], "700.HK")
        self.assertEqual(order["direction"], "buy")
        self.assertEqual(order["quantity"], 100)
        self.assertEqual(order["price"], 500.0)
        
        # 验证方法调用
        self.mock_trade_ctx.submit_order.assert_called_once()
    
    def test_cancel_order(self):
        """测试撤单"""
        # 模拟撤单返回值
        self.mock_trade_ctx.cancel_order.return_value = None  # 假设成功撤单时无返回值
        
        # 调用接口撤单
        result = self.interface.cancel_order(self.mock_account_id, "test_order_id")
        
        # 验证结果
        self.assertTrue(result)
        
        # 验证方法调用
        self.mock_trade_ctx.cancel_order.assert_called_once_with(order_id="test_order_id")
    
    def test_get_market_data(self):
        """测试获取市场数据"""
        # 模拟行情返回值
        mock_quote = MagicMock()
        mock_quote.symbol = "700.HK"
        mock_quote.last_done = Decimal("550.0")
        mock_quote.ask_price = Decimal("551.0")
        mock_quote.bid_price = Decimal("549.0")
        mock_quote.volume = 1000000
        mock_quote.turnover = Decimal("550000000.0")
        mock_quote.timestamp = "2023-01-01T00:00:00.000Z"
        
        self.mock_quote_ctx.quote.return_value = [mock_quote]
        
        # 调用接口获取市场数据
        market_data = self.interface.get_market_data(["700.HK"])
        
        # 验证结果
        self.assertIsNotNone(market_data)
        self.assertIn("700.HK", market_data)
        self.assertEqual(market_data["700.HK"]["last_price"], 550.0)
        self.assertEqual(market_data["700.HK"]["ask_price"], 551.0)
        self.assertEqual(market_data["700.HK"]["bid_price"], 549.0)
        
        # 验证方法调用
        self.mock_quote_ctx.quote.assert_called_once()

if __name__ == "__main__":
    unittest.main() 