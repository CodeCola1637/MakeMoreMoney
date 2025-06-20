#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
风控机制测试脚本
验证修复后的风控机制是否正常工作
"""

import os
import sys
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# 添加项目根目录到Python路径
sys.path.append('.')

from utils import ConfigLoader, setup_logger, setup_longport_env
from execution.order_manager import OrderManager
from strategy.signals import Signal, SignalType

async def test_risk_control():
    """测试风控机制"""
    print("🔧 风控机制测试开始...")
    
    # 加载环境变量和配置
    load_dotenv()
    setup_longport_env()
    config = ConfigLoader()
    
    # 设置日志
    logger = setup_logger("risk_test", "DEBUG")
    
    # 初始化订单管理器
    order_mgr = OrderManager(config)
    await order_mgr.initialize()
    
    print("\n📊 当前配置:")
    print(f"   position_pct: {config.get('execution.risk_control.position_pct')}%")
    print(f"   max_position_size: {config.get('execution.max_position_size')}")
    print(f"   max_position_weight: {config.get('portfolio.max_position_weight')}")
    
    print("\n💰 账户状态:")
    balance = order_mgr.get_account_balance()
    print(f"   可用资金: ${balance:.2f}")
    
    positions = order_mgr.get_positions()
    print(f"   持仓数量: {len(positions) if positions else 0}")
    for pos in (positions or []):
        print(f"   - {pos.symbol}: {pos.quantity}股")
    
    print("\n🧪 测试用例:")
    
    # 测试用例1: 正常小额买入（应该通过）
    print("\n1️⃣ 测试小额买入（应该通过）")
    test_signal1 = Signal(
        symbol="AAPL.US",
        signal_type=SignalType.BUY,
        quantity=5,  # 小数量
        price=200.0,
        confidence=0.7,
        strategy_name="test_small"
    )
    
    result1 = await order_mgr.execute_signal(test_signal1)
    if result1:
        print(f"   ✅ 小额买入测试: {result1.status} - {result1.msg}")
    else:
        print(f"   ❌ 小额买入测试: 无结果返回")
    
    # 测试用例2: 大额买入（应该被限制）
    print("\n2️⃣ 测试大额买入（应该被限制）")
    test_signal2 = Signal(
        symbol="NVDA.US",
        signal_type=SignalType.BUY,
        quantity=500,  # 大数量
        price=145.0,
        confidence=0.8,
        strategy_name="test_large"
    )
    
    result2 = await order_mgr.execute_signal(test_signal2)
    if result2:
        print(f"   🛡️ 大额买入测试: {result2.status} - {result2.msg}")
        if result2.quantity < test_signal2.quantity:
            print(f"   ✅ 数量已被调整: {test_signal2.quantity} -> {result2.quantity}")
    else:
        print(f"   ❌ 大额买入测试: 无结果返回")
    
    # 测试用例3: 超大额买入（应该被拒绝）
    print("\n3️⃣ 测试超大额买入（应该被拒绝）")
    test_signal3 = Signal(
        symbol="TSLA.US",
        signal_type=SignalType.BUY,
        quantity=1000,  # 超大数量
        price=300.0,
        confidence=0.9,
        strategy_name="test_huge"
    )
    
    result3 = await order_mgr.execute_signal(test_signal3)
    if result3:
        print(f"   🚫 超大额买入测试: {result3.status} - {result3.msg}")
        if str(result3.status) == "OrderStatus.Rejected":
            print(f"   ✅ 风控正常工作，已拒绝超大额交易")
    else:
        print(f"   ✅ 超大额买入测试: 被系统拒绝")
    
    print("\n📈 风控测试总结:")
    print("   - 小额交易应该通过或有限制地通过")
    print("   - 大额交易应该被调整到合理数量")
    print("   - 超大额交易应该被拒绝")
    print("   - 负余额情况下应该严格限制交易")
    
    # 清理
    await order_mgr.close()
    print("\n✅ 风控测试完成")

if __name__ == "__main__":
    asyncio.run(test_risk_control()) 