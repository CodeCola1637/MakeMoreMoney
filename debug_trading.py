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

async def debug_quote_callback(symbol: str, quote):
    """调试用的行情回调函数"""
    logger.info(f"[DEBUG] 收到行情推送: {symbol}")
    logger.info(f"[DEBUG] 价格详情: last_done={quote.last_done}, open={quote.open}, high={quote.high}, low={quote.low}, volume={quote.volume}")

async def debug_signal_callback(signal_obj: Signal):
    """调试用的信号回调函数"""
    logger.info(f"[DEBUG] 收到交易信号: {signal_obj}")
    logger.info(f"[DEBUG] 信号详情: 股票={signal_obj.symbol}, 类型={signal_obj.signal_type}, 价格={signal_obj.price}, 数量={signal_obj.quantity}")

async def main():
    global logger, should_continue
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="调试版量化交易系统")
    parser.add_argument("--symbols", nargs="+", default=["700.HK"], help="要交易的股票代码")
    parser.add_argument("--train", action="store_true", help="训练模型")
    args = parser.parse_args()
    
    # 加载环境变量
    load_dotenv()
    setup_longport_env()
    
    # 设置调试级别日志
    logger = setup_logger("debug_trading", "DEBUG")
    logger.info("[DEBUG] 开始启动调试版交易系统")
    
    # 加载配置
    config = ConfigLoader()
    config.update_config('quote.use_mock_data', False)
    config.update_config('logging.level', 'DEBUG')
    logger.info("[DEBUG] 配置加载完成，已禁用模拟数据")
    
    try:
        # 初始化实时数据管理器
        logger.info("[DEBUG] 步骤 1: 初始化实时数据管理器")
        realtime_mgr = RealtimeDataManager(config)
        await realtime_mgr.initialize()
        logger.info("[DEBUG] 实时数据管理器初始化成功")
        
        # 注册调试回调
        realtime_mgr.register_callback("Quote", debug_quote_callback)
        logger.info("[DEBUG] 已注册调试用行情回调")
        
        # 初始化历史数据加载器
        logger.info("[DEBUG] 步骤 2: 初始化历史数据加载器")
        hist_loader = HistoricalDataLoader(config)
        
        # 初始化模型训练器
        logger.info("[DEBUG] 步骤 3: 初始化模型训练器")
        model_trainer = LSTMModelTrainer(config, hist_loader)
        
        # 如果需要训练模型
        if args.train:
            logger.info("[DEBUG] 开始训练模型")
            await model_trainer.train_model(args.symbols)
            logger.info("[DEBUG] 模型训练完成")
        
        # 初始化信号生成器
        logger.info("[DEBUG] 步骤 4: 初始化信号生成器")
        signal_gen = SignalGenerator(config, realtime_mgr, model_trainer)
        await signal_gen.start()
        logger.info("[DEBUG] 信号生成器启动成功")
        
        # 注册信号回调
        signal_gen.register_callback(debug_signal_callback)
        logger.info("[DEBUG] 已注册调试用信号回调")
        
        # 初始化订单管理器
        logger.info("[DEBUG] 步骤 5: 初始化订单管理器")
        order_mgr = OrderManager(config)
        await order_mgr.initialize()
        logger.info("[DEBUG] 订单管理器初始化成功")
        
        # 获取账户信息
        logger.info("[DEBUG] 步骤 6: 获取账户信息")
        try:
            balance = order_mgr.get_account_balance()
            logger.info(f"[DEBUG] 账户余额: {balance}")
            
            positions = order_mgr.get_positions()
            logger.info(f"[DEBUG] 当前持仓: {positions}")
        except Exception as e:
            logger.warning(f"[DEBUG] 获取账户信息失败: {e}")
        
        # 订阅股票实时数据
        logger.info("[DEBUG] 步骤 7: 订阅股票实时数据")
        for symbol in args.symbols:
            try:
                await realtime_mgr.subscribe([symbol], [SubType.Quote])
                logger.info(f"[DEBUG] 成功订阅 {symbol} 行情数据")
                
                # 尝试获取初始价格
                quotes = await realtime_mgr.get_quote([symbol])
                if quotes and symbol in quotes:
                    logger.info(f"[DEBUG] {symbol} 当前价格: {quotes[symbol].last_done}")
                else:
                    logger.warning(f"[DEBUG] 无法获取 {symbol} 的初始价格")
                    
            except Exception as e:
                logger.error(f"[DEBUG] 订阅 {symbol} 失败: {e}")
        
        # 启动定时信号生成任务
        logger.info("[DEBUG] 步骤 8: 启动定时信号生成任务")
        signal_interval = config.get("strategy.signal_interval", 30)
        logger.info(f"[DEBUG] 信号生成间隔: {signal_interval} 秒")
        
        # 创建信号生成任务
        async def debug_signal_task():
            try:
                logger.info("[DEBUG] 定时信号生成任务开始运行")
                await signal_gen.scheduled_signal_generation(interval_seconds=signal_interval)
            except Exception as e:
                logger.error(f"[DEBUG] 定时信号生成任务异常: {e}")
                import traceback
                logger.error(f"[DEBUG] 异常详情: {traceback.format_exc()}")
        
        signal_task = asyncio.create_task(debug_signal_task())
        logger.info("[DEBUG] 定时信号生成任务已启动")
        
        # 主监控循环
        logger.info("[DEBUG] 系统启动完成，开始主监控循环")
        loop_count = 0
        while should_continue:
            loop_count += 1
            
            # 每30秒输出一次状态
            if loop_count % 30 == 0:
                logger.info(f"[DEBUG] 系统运行状态检查 (循环 {loop_count})")
                
                # 检查数据缓存状态
                if hasattr(signal_gen, 'data_cache'):
                    cache_status = {}
                    for symbol, data in signal_gen.data_cache.items():
                        cache_status[symbol] = len(data)
                    logger.info(f"[DEBUG] 数据缓存状态: {cache_status}")
                
                # 检查最新行情
                for symbol in args.symbols:
                    quote = realtime_mgr.get_latest_quote(symbol)
                    if quote:
                        logger.info(f"[DEBUG] {symbol} 最新行情: {quote.last_done}")
                    else:
                        logger.warning(f"[DEBUG] {symbol} 无最新行情数据")
            
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("[DEBUG] 收到中断信号，开始关闭系统")
        should_continue = False
    except Exception as e:
        logger.error(f"[DEBUG] 系统运行异常: {e}")
        import traceback
        logger.error(f"[DEBUG] 异常详情: {traceback.format_exc()}")
    finally:
        # 清理资源
        logger.info("[DEBUG] 开始清理系统资源")
        try:
            if 'signal_task' in locals() and not signal_task.done():
                signal_task.cancel()
                try:
                    await signal_task
                except asyncio.CancelledError:
                    pass
            
            if 'realtime_mgr' in locals():
                await realtime_mgr.stop()
                logger.info("[DEBUG] 实时数据管理器已关闭")
            
            if 'order_mgr' in locals():
                await order_mgr.close()
                logger.info("[DEBUG] 订单管理器已关闭")
                
        except Exception as e:
            logger.error(f"[DEBUG] 清理资源时出错: {e}")
        
        logger.info("[DEBUG] 系统已安全关闭")

if __name__ == "__main__":
    # 设置信号处理
    def handle_signal(signum, frame):
        global should_continue
        should_continue = False
        print(f"\n[DEBUG] 接收到信号 {signum}，准备关闭系统...")
    
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    # 运行调试系统
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"[DEBUG] 主程序异常退出: {e}")
        import traceback
        traceback.print_exc() 