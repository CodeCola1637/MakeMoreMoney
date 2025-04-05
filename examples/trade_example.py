#!/usr/bin/env python3
"""
演示如何使用LongPortClient类进行交易和获取实时数据
"""

import os
import sys
import time
import logging
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import signal

# 添加父目录到路径，以便导入LongPortClient
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from longbridge_quant.api_client.client import LongPortClient
from longport.openapi import (
    OrderSide, 
    OrderType, 
    TimeInForceType, 
    SubType, 
    Period,
    AdjustType,
    Market
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("trade_example")

# 全局变量，用于示例程序的控制流
running = True
client = None

def signal_handler(sig, frame):
    """处理终端信号，优雅地关闭程序"""
    global running
    logger.info("接收到终止信号，准备关闭程序...")
    running = False
    if client:
        logger.info("关闭API连接...")
        client.close()
    sys.exit(0)

def quote_update_callback(symbol, event):
    """报价更新回调函数"""
    logger.info(f"收到 {symbol} 的报价更新: 最新价格 {event.last_done}, 交易量 {event.volume}")

def trade_update_callback(symbol, event):
    """交易更新回调函数"""
    logger.info(f"收到 {symbol} 的成交更新: 价格 {event.price}, 数量 {event.volume}, 方向 {event.direct}")

def depth_update_callback(symbol, event):
    """深度更新回调函数"""
    logger.info(f"收到 {symbol} 的深度更新: 买盘档位 {len(event.bids)}, 卖盘档位 {len(event.asks)}")

async def monitor_positions():
    """监控持仓示例"""
    global client, running
    try:
        while running:
            positions = client.get_positions()
            logger.info(f"当前持仓: {positions}")
            
            # 监控账户余额
            balances = client.get_account_balance()
            logger.info(f"账户余额: {balances}")
            
            # 每30秒检查一次
            await asyncio.sleep(30)
    except Exception as e:
        logger.error(f"监控持仓出错: {e}")

async def market_data_demo():
    """市场数据获取示例"""
    global client, running
    try:
        # 获取交易时段信息
        sessions = client.get_trading_sessions()
        logger.info(f"交易时段信息: {sessions}")
        
        # 获取股票基本信息
        symbols = ["700.HK", "AAPL.US"]
        info = client.get_stock_info(symbols)
        logger.info(f"股票基本信息: {info}")
        
        # 获取实时报价
        quotes = client.get_quote(symbols)
        logger.info(f"实时报价: {quotes}")
        
        # 获取K线数据
        symbol = "700.HK"
        candles = client.get_candlesticks(
            symbol=symbol,
            period=Period.Day,
            count=10,
            adjust_type=AdjustType.NoAdjust
        )
        logger.info(f"{symbol} 的K线数据: {candles}")
        
        # 获取历史K线数据
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        hist_candles = client.get_history_candlesticks(
            symbol=symbol,
            period=Period.Day,
            adjust_type=AdjustType.NoAdjust,
            start_date=start_date,
            end_date=end_date
        )
        logger.info(f"{symbol} 的历史K线数据: {len(hist_candles)} 条记录")
        
    except Exception as e:
        logger.error(f"市场数据获取示例出错: {e}")

async def order_demo():
    """交易示例"""
    global client, running
    try:
        # 获取订单历史
        orders = client.get_today_orders()
        logger.info(f"今日订单: {orders}")
        
        # 是否执行下单 (演示目的，设为False避免实际下单)
        execute_order = False
        
        if execute_order:
            # 创建限价买单示例
            symbol = "700.HK"
            price = Decimal("500.00")  # 确保是合理的限价
            quantity = 100  # 股数
            
            order_result = client.create_order(
                symbol=symbol,
                order_type=OrderType.LO,  # 限价单
                side=OrderSide.Buy,
                quantity=quantity,
                time_in_force=TimeInForceType.Day,
                submitted_price=price,
                remark="测试API订单"
            )
            logger.info(f"创建订单结果: {order_result}")
            
            # 等待5秒后撤单
            await asyncio.sleep(5)
            
            # 撤销刚才的订单
            cancel_result = client.cancel_order(order_result.order_id)
            logger.info(f"撤单结果: {cancel_result}")
        
    except Exception as e:
        logger.error(f"交易示例出错: {e}")

async def subscription_demo():
    """实时数据订阅示例"""
    global client, running
    try:
        # 订阅的股票列表
        symbols = ["700.HK", "AAPL.US"]
        
        # 注册回调函数
        for symbol in symbols:
            client.register_quote_callback(symbol, quote_update_callback)
            client.register_trade_callback(symbol, trade_update_callback)
            client.register_depth_callback(symbol, depth_update_callback)
        
        # 订阅行情、逐笔成交和深度数据
        sub_types = [SubType.Quote, SubType.Trade, SubType.Depth]
        subscription = client.subscribe_quotes(symbols, sub_types)
        logger.info(f"订阅结果: {subscription}")
        
        # 保持程序运行，持续接收WebSocket数据
        while running:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"实时数据订阅示例出错: {e}")
    finally:
        # 取消订阅
        try:
            client.unsubscribe_quotes(symbols, sub_types)
            logger.info("已取消所有订阅")
        except Exception as e:
            logger.error(f"取消订阅出错: {e}")

async def main():
    """主函数"""
    global client, running
    
    # 创建客户端
    client = LongPortClient(use_websocket=True)
    
    try:
        # 执行市场数据演示
        await market_data_demo()
        
        # 执行交易演示
        await order_demo()
        
        # 启动持仓监控
        monitor_task = asyncio.create_task(monitor_positions())
        
        # 执行实时数据订阅演示
        await subscription_demo()
        
        # 取消监控任务
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
            
    except Exception as e:
        logger.error(f"执行示例时出错: {e}")
    finally:
        # 清理资源
        if client:
            client.close()
        logger.info("示例完成")

if __name__ == "__main__":
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 运行主程序
    asyncio.run(main()) 