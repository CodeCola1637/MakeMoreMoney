#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
测试长桥客户端连接
"""

import os
import sys
import logging
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_client")

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# 导入LongPortClient
try:
    from longbridge_quant.api_client.client import LongPortClient
    from longport.openapi import OrderSide, OrderType, TimeInForceType
    logger.info("成功导入LongPortClient")
except ImportError as e:
    logger.error(f"导入LongPortClient失败: {e}")
    sys.exit(1)

def main():
    """测试长桥客户端"""
    try:
        # 初始化客户端
        client = LongPortClient(use_websocket=True)
        logger.info("成功创建LongPortClient实例")
        
        # 测试连接
        logger.info("测试交易上下文")
        _ = client.trade_ctx
        logger.info("交易上下文创建成功")
        
        logger.info("测试行情上下文")
        _ = client.quote_ctx
        logger.info("行情上下文创建成功")
        
        # 获取账户余额
        logger.info("获取账户余额")
        balance = client.get_account_balance()
        logger.info(f"账户余额: {balance}")
        if balance and len(balance) > 0:
            logger.info("账户余额详情:")
            for attr in dir(balance[0]):
                if not attr.startswith('_'):
                    value = getattr(balance[0], attr)
                    logger.info(f"属性 {attr}: {value} (类型: {type(value)})")
        
        # 获取今日订单
        logger.info("获取今日订单")
        today_orders = client.get_today_orders()
        logger.info(f"获取到 {len(today_orders)} 个订单")
        if today_orders:
            logger.info("订单详情:")
            for i, order in enumerate(today_orders):
                logger.info(f"订单 {i+1}:")
                logger.info(f"订单ID: {order.order_id}")
                logger.info(f"股票: {order.symbol}")
                logger.info(f"状态: {order.status}")
                logger.info(f"订单类型: {order.order_type}")
                
                # 逐个打印所有属性和类型
                logger.info("所有属性:")
                for attr in dir(order):
                    if not attr.startswith('_'):
                        try:
                            value = getattr(order, attr)
                            logger.info(f"  - {attr}: {value} (类型: {type(value)})")
                        except Exception as e:
                            logger.info(f"  - {attr}: 无法访问 ({e})")
                            
                logger.info("-" * 50)
        
        # 创建订单
        logger.info("测试创建订单")
        symbol = "700.HK"
        side = OrderSide.Buy
        order_type = OrderType.LO
        quantity = 100
        submitted_price = 550.0
        time_in_force = TimeInForceType.Day
        
        logger.info(f"创建订单: {symbol} {side} {quantity}@{submitted_price}")
        order_result = client.create_order(
            symbol=symbol,
            order_type=order_type,
            side=side,
            quantity=quantity,
            time_in_force=time_in_force,
            submitted_price=submitted_price
        )
        logger.info(f"订单创建结果: {order_result}")
        logger.info(f"订单ID: {order_result.order_id}")
        
        logger.info("测试完成")
    except Exception as e:
        logger.error(f"测试过程中出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main() 