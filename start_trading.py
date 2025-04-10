#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
import sys
import traceback
from datetime import datetime
from typing import Any

from data_loader.realtime import RealtimeDataManager
from data_loader.historical import HistoricalDataLoader
from strategy.signals import SignalGenerator
from strategy.train import LSTMModelTrainer
from execution.order_manager import OrderManager
from utils import ConfigLoader, setup_logger
from execution.trade_executor import TradeExecutor

async def main():
    try:
        # 加载配置
        config = ConfigLoader()
        
        # 设置日志
        log_dir = config.get('logging', {}).get('dir', 'logs')
        os.makedirs(log_dir, exist_ok=True)
        
        log_file = os.path.join(log_dir, f'trading_{datetime.now().strftime("%Y%m%d")}.log')
        logger = setup_logger('trading', config.get('logging', {}).get('level', 'INFO'), log_file)
        
        logger.info("开始初始化交易系统...")
        
        # 初始化实时数据管理器
        realtime_mgr = RealtimeDataManager(config)
        await realtime_mgr.initialize()
        
        # 初始化历史数据加载器
        historical_loader = HistoricalDataLoader(config)
        
        # 初始化模型训练器
        model_trainer = LSTMModelTrainer(config, historical_loader)
        
        # 初始化信号生成器
        signal_generator = SignalGenerator(config, realtime_mgr, model_trainer)
        realtime_mgr.signal_generator = signal_generator  # 设置信号生成器引用
        
        # 初始化订单管理器
        order_mgr = OrderManager(config)
        await order_mgr.initialize()
        
        # 注册信号回调
        def on_signal(signal):
            asyncio.create_task(order_mgr.process_signal(signal))
        
        signal_generator.register_callback(on_signal)
        
        # 注册实时数据回调
        def on_quote(symbol: str, quote: Any):
            signal_generator.update_data(symbol, quote)
        
        realtime_mgr.register_callback("Quote", on_quote)
        
        # 订阅股票
        symbols = config.get('quote', {}).get('symbols', [])
        if not symbols:
            logger.error("未配置交易股票代码")
            return
            
        logger.info(f"订阅股票: {symbols}")
        await realtime_mgr.subscribe(symbols)
        
        # 启动信号生成器
        asyncio.create_task(signal_generator.start())
        
        # 启动交易执行器
        executor = TradeExecutor(config, realtime_mgr)
        await executor.start()
        
        # 保持程序运行
        logger.info("交易系统已启动，等待信号...")
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在关闭系统...")
    except Exception as e:
        logger.error(f"系统运行出错: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
    finally:
        # 清理资源
        if 'realtime_mgr' in locals():
            await realtime_mgr.close()
        if 'order_mgr' in locals():
            await order_mgr.close()
        logger.info("交易系统已关闭")

if __name__ == "__main__":
    asyncio.run(main()) 