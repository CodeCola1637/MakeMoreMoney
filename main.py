#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import time
import logging
import asyncio
import signal
import argparse
from dotenv import load_dotenv
from longport.openapi import SubType

from utils import ConfigLoader, setup_logger, setup_longport_env
from databases.db import init_db
from data_loader.realtime import RealtimeDataManager
from data_loader.historical import HistoricalDataLoader
from strategy.train import LSTMModelTrainer
from strategy.signals import SignalGenerator, Signal
from execution.order_manager import OrderManager, OrderResult

# 全局变量
should_continue = True
logger = None

# 创建信号处理回调函数
def create_signal_handler(order_mgr):
    """创建交易信号处理函数"""
    async def on_signal(signal_obj: Signal):
        """处理交易信号"""
        try:
            symbol = signal_obj.symbol
            signal_type = signal_obj.signal_type
            # 安全地获取signal_type的value属性
            signal_type_val = signal_type.value if hasattr(signal_type, 'value') else str(signal_type)
            price = signal_obj.price
            quantity = signal_obj.quantity
            
            # 使用安全获取的枚举值字符串进行信息记录
            logger.info(f"收到交易信号: {symbol} {signal_type_val} {quantity}股 @ {price}")
            
            # 触发订单执行
            await order_mgr.process_signal(signal_obj)
        except Exception as e:
            logger.error(f"处理信号时出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    return on_signal

# 创建订单更新回调
def create_order_update_handler():
    """创建订单状态更新处理函数"""
    def on_order_update(order_result: OrderResult):
        """处理订单状态更新"""
        logger.info(f"订单状态更新: {order_result}")
    
    return on_order_update

# 关闭处理
def shutdown():
    """处理程序关闭"""
    global should_continue
    should_continue = False
    logger.info("接收到关闭信号")

# 信号处理
def setup_signal_handlers():
    """设置信号处理器"""
    signal.signal(signal.SIGINT, lambda s, f: shutdown())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown())

async def main():
    global logger
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="量化交易系统")
    parser.add_argument("--train", action="store_true", help="训练模型")
    parser.add_argument("--symbols", nargs="+", default=["700.HK"], help="要交易的股票代码")
    parser.add_argument("--no-mock", action="store_true", help="禁止使用模拟数据，使用实时行情")
    args = parser.parse_args()
    
    # 加载环境变量
    load_dotenv()
    
    # 设置长桥API环境变量
    setup_longport_env()
    
    # 设置信号处理
    setup_signal_handlers()
    
    # 设置日志
    logger = setup_logger("main", "INFO")
    logger.info("设置环境变量和日志完成")
    
    # 加载配置
    config = ConfigLoader()
    
    # 设置禁用模拟数据
    if args.no_mock:
        config.update_config('quote.use_mock_data', False)
        logger.info("已禁用模拟数据，使用实时行情")
    
    # 初始化数据库
    init_db()
    
    # 初始化组件
    logger.info("初始化系统组件...")
    
    # 初始化实时数据管理器 - 使用真实API
    try:
        realtime_mgr = RealtimeDataManager(config)
        logger.info("实时数据管理器初始化完成")
    except Exception as e:
        logger.error(f"实时数据管理器初始化失败: {e}")
        return
        
    # 初始化历史数据加载器
    hist_loader = HistoricalDataLoader(config)
    logger.info("历史数据加载器初始化完成")
    
    # 初始化模型训练器
    model_trainer = LSTMModelTrainer(config, hist_loader)
    logger.info("LSTM模型训练器初始化完成")
    
    # 初始化信号生成器
    signal_gen = SignalGenerator(config, realtime_mgr, model_trainer)
    logger.info("信号生成器初始化完成")
    
    # 初始化订单管理器 - 使用真实API
    try:
        order_mgr = OrderManager(config)
        logger.info("订单管理器初始化完成")
    except Exception as e:
        logger.error(f"订单管理器初始化失败: {e}")
        return
    
    # 创建回调处理函数
    on_signal = create_signal_handler(order_mgr)
    on_order_update = create_order_update_handler()
    
    # 如果指定了训练模式，则先训练模型
    if args.train:
        logger.info("开始训练模型...")
        for symbol in args.symbols:
            # 加载历史数据
            try:
                hist_data = await hist_loader.get_candlesticks(symbol)
                if hist_data.empty:
                    logger.warning(f"无法获取{symbol}的历史数据，跳过训练")
                    continue
                    
                # 训练模型
                await model_trainer.train_model([symbol])
                logger.info(f"{symbol}的模型训练完成")
            except Exception as e:
                logger.error(f"训练{symbol}的模型时出错: {e}")
    
    # 注册回调和启动组件
    signal_gen.register_signal_callback(on_signal)
    order_mgr.register_order_callback(on_order_update)
    
    # 启动实时数据管理器
    try:
        logger.info("正在初始化实时数据管理器...")
        await realtime_mgr.initialize()
        logger.info("实时数据管理器初始化成功")
        
        # 初始化订单管理器
        logger.info("正在初始化订单管理器...")
        await order_mgr.initialize()
        logger.info("订单管理器初始化成功")
        
        # 获取账户信息
        try:
            balance = await order_mgr.get_account_balance()
            if balance:
                logger.info(f"账户余额: {balance}")
            
            positions = await order_mgr.get_positions()
            if positions:
                logger.info(f"当前持仓: {positions}")
        except Exception as e:
            logger.warning(f"获取账户信息失败: {e}")
        
        # 订阅股票实时数据
        logger.info("正在订阅股票实时数据...")
        for symbol in args.symbols:
            try:
                # 使用SubType.Quote枚举
                await realtime_mgr.subscribe([symbol], [SubType.Quote])
                
                # 获取并记录初始价格
                try:
                    quotes = await realtime_mgr.get_quote([symbol])
                    if quotes and symbol in quotes:
                        initial_price = quotes[symbol].last_done
                        logger.info(f"初始价格 {symbol}: {initial_price}")
                except Exception as e:
                    logger.warning(f"获取{symbol}初始价格失败: {e}")
                    
                logger.info(f"已订阅 {symbol} 行情数据")
            except Exception as e:
                logger.error(f"订阅 {symbol} 行情数据失败: {e}")
        
        # 启动信号生成器的定时任务
        logger.info("启动信号生成器的定时任务...")
        signal_task = asyncio.create_task(signal_gen.scheduled_signal_generation(interval_seconds=60))
        
        logger.info("系统已启动并运行中...")
        
        # 主循环
        while should_continue:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"运行时错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 关闭所有组件
        logger.info("关闭所有组件...")
        try:
            await realtime_mgr.stop()
            logger.info("实时数据管理器已关闭")
        except Exception as e:
            logger.error(f"关闭行情管理器错误: {e}")
            
        try:
            await order_mgr.close()
            logger.info("订单管理器已关闭")
        except Exception as e:
            logger.error(f"关闭订单管理器错误: {e}")
            
        # 取消信号生成任务
        if 'signal_task' in locals() and not signal_task.done():
            signal_task.cancel()
            try:
                await signal_task
            except asyncio.CancelledError:
                pass
            
        logger.info("系统已安全关闭")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close() 