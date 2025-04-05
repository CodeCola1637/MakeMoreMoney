#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试交易执行服务器
"""

import os
import sys
import unittest
import json
from unittest.mock import patch, MagicMock
import threading
import time
import requests

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from quant_project.trading_execution.trading_server import (
    TradingServer, OrderRequest, SignalRequest, StrategyRequest, RiskLimitsRequest
)

class TestTradingServer(unittest.TestCase):
    """测试交易执行服务器"""
    
    def setUp(self):
        """测试前准备"""
        self.server = TradingServer()
        
        # 模拟交易接口
        self.mock_interface = MagicMock()
        self.server.trading_interfaces = {
            "mock": self.mock_interface
        }
        self.server.active_interface = "mock"
        
        # 模拟账户信息
        mock_account = {
            "account_id": "test_account",
            "balance": 1000000.0,
            "positions": {},
            "status": "active"
        }
        self.mock_interface.get_account.return_value = mock_account
        
        # 模拟持仓信息
        mock_positions = [
            {
                "account_id": "test_account",
                "symbol": "700.HK",
                "quantity": 100,
                "average_cost": 500.0,
                "current_price": 550.0,
                "market_value": 55000.0,
                "unrealized_pnl": 5000.0
            }
        ]
        self.mock_interface.get_positions.return_value = mock_positions
        
        # 模拟订单信息
        mock_orders = [
            {
                "order_id": "test_order",
                "account_id": "test_account",
                "symbol": "700.HK",
                "direction": "buy",
                "quantity": 100,
                "price": 500.0,
                "status": "filled",
                "filled_quantity": 100
            }
        ]
        self.mock_interface.get_orders.return_value = mock_orders
        
        # 模拟市场数据
        mock_market_data = {
            "700.HK": {
                "symbol": "700.HK",
                "last_price": 550.0,
                "bid_price": 549.0,
                "ask_price": 551.0
            }
        }
        self.mock_interface.get_market_data.return_value = mock_market_data
        
        # 模拟下单结果
        mock_order_result = {
            "order_id": "new_test_order",
            "account_id": "test_account",
            "symbol": "700.HK",
            "direction": "buy",
            "quantity": 100,
            "price": 500.0,
            "status": "pending"
        }
        self.mock_interface.place_order.return_value = mock_order_result
        
        # 模拟撤单结果
        self.mock_interface.cancel_order.return_value = True
    
    def test_get_account_info(self):
        """测试获取账户信息"""
        account = self.server.get_account_info()
        self.assertIsNotNone(account)
        self.assertEqual(account["balance"], 1000000.0)
        self.assertEqual(account["status"], "active")
        
        # 验证方法调用
        self.mock_interface.get_account.assert_called_once()
    
    def test_get_positions(self):
        """测试获取持仓信息"""
        positions = self.server.get_positions()
        self.assertIsNotNone(positions)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "700.HK")
        
        # 验证方法调用
        self.mock_interface.get_positions.assert_called_once()
    
    def test_get_orders(self):
        """测试获取订单信息"""
        orders = self.server.get_orders()
        self.assertIsNotNone(orders)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["symbol"], "700.HK")
        
        # 验证方法调用
        self.mock_interface.get_orders.assert_called_once()
    
    def test_place_order(self):
        """测试下单"""
        # 创建下单请求
        order_request = OrderRequest(
            symbol="700.HK",
            direction="buy",
            quantity=100,
            price=500.0,
            order_type="limit"
        )
        
        # 执行下单
        order = self.server.place_order(order_request)
        
        # 验证结果
        self.assertIsNotNone(order)
        self.assertEqual(order["order_id"], "new_test_order")
        self.assertEqual(order["symbol"], "700.HK")
        
        # 验证方法调用
        self.mock_interface.place_order.assert_called_once()
    
    def test_cancel_order(self):
        """测试撤单"""
        # 执行撤单
        result = self.server.cancel_order("test_order")
        
        # 验证结果
        self.assertTrue(result)
        
        # 验证方法调用
        self.mock_interface.cancel_order.assert_called_once()
    
    def test_process_signal(self):
        """测试处理交易信号"""
        # 创建交易信号
        signal = SignalRequest(
            signal_id="test_signal",
            strategy_id="test_strategy",
            symbol="700.HK",
            direction="buy",
            quantity=100,
            price=500.0,
            reason="测试信号"
        )
        
        # 处理信号
        result = self.server.process_signal(signal)
        
        # 验证结果
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")
        
        # 验证方法调用
        self.mock_interface.place_order.assert_called_once()
    
    def test_register_strategy(self):
        """测试注册策略"""
        # 创建策略请求
        strategy = StrategyRequest(
            strategy_id="test_strategy",
            name="测试策略",
            description="这是一个测试策略",
            symbols=["700.HK", "AAPL.US"],
            interval="1d",
            parameters={"param1": 1, "param2": "test"}
        )
        
        # 注册策略
        result = self.server.register_strategy(strategy)
        
        # 验证结果
        self.assertIsNotNone(result)
        self.assertEqual(result["strategy_id"], "test_strategy")
        
        # 验证策略是否已注册
        self.assertIn("test_strategy", self.server.strategies)
    
    def test_update_risk_limits(self):
        """测试更新风险限制"""
        # 创建风险限制请求
        limits = RiskLimitsRequest(
            max_position_value=1000000.0,
            max_daily_orders=100,
            max_daily_turnover=500000.0,
            max_order_value=50000.0,
            max_drawdown=0.1
        )
        
        # 更新风险限制
        result = self.server.update_risk_limits(limits)
        
        # 验证结果
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")
        
        # 验证风险限制是否已更新
        self.assertEqual(self.server.risk_limits["max_position_value"], 1000000.0)
        self.assertEqual(self.server.risk_limits["max_daily_orders"], 100)
        self.assertEqual(self.server.risk_limits["max_daily_turnover"], 500000.0)
        self.assertEqual(self.server.risk_limits["max_order_value"], 50000.0)
        self.assertEqual(self.server.risk_limits["max_drawdown"], 0.1)
    
    def test_enable_disable_trading(self):
        """测试启用和禁用交易"""
        # 默认情况下，交易应该是启用的
        self.assertTrue(self.server.trading_enabled)
        
        # 禁用交易
        self.server.disable_trading()
        self.assertFalse(self.server.trading_enabled)
        
        # 尝试下单，应该失败
        order_request = OrderRequest(
            symbol="700.HK",
            direction="buy",
            quantity=100,
            price=500.0,
            order_type="limit"
        )
        
        result = self.server.place_order(order_request)
        self.assertIsNone(result)  # 交易禁用时，下单应该返回None
        
        # 启用交易
        self.server.enable_trading()
        self.assertTrue(self.server.trading_enabled)
        
        # 尝试下单，应该成功
        result = self.server.place_order(order_request)
        self.assertIsNotNone(result)

if __name__ == "__main__":
    unittest.main() 