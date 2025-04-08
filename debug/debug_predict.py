#!/usr/bin/env python
import asyncio
import sys
import traceback
from utils import ConfigLoader
from data_loader.realtime import RealtimeDataManager
from data_loader.historical import HistoricalDataLoader
from strategy.train import LSTMModelTrainer
from strategy.signals import SignalGenerator, SignalType, Signal

async def test_predict_signal():
    try:
        print("初始化配置...")
        config = ConfigLoader("config.yaml")
        
        print("初始化数据管理器...")
        historical_data = HistoricalDataLoader(config)
        realtime_data = RealtimeDataManager(config)
        
        print("初始化模型训练器...")
        model_trainer = LSTMModelTrainer(config, historical_data)
        
        print("初始化信号生成器...")
        signal_generator = SignalGenerator(config, realtime_data, model_trainer)
        
        # 初始化各组件
        print("初始化历史数据管理器...")
        await historical_data.initialize()
        
        print("初始化行情数据管理器...")
        await realtime_data.initialize()
        
        # 订阅股票
        symbol = "700.HK"
        print(f"订阅股票 {symbol}...")
        from longport.openapi import SubType
        await realtime_data.subscribe([symbol], [SubType.Quote], True)
        
        # 设置回调函数
        def on_signal(signal):
            print(f"收到信号: {signal}")
            print(f"信号类型: {signal.signal_type}, 类型: {type(signal.signal_type)}")
        
        signal_generator.register_signal_callback(on_signal)
        
        # 生成信号
        print(f"为 {symbol} 生成信号...")
        signal = await signal_generator.predict_and_generate_signal(symbol)
        
        if signal:
            print(f"成功生成信号: {signal}")
            print(f"信号类型: {signal.signal_type}, 类型: {type(signal.signal_type)}")
            print(f"信号字典: {signal.to_dict()}")
        else:
            print("未生成信号")
        
        # 关闭连接
        print("关闭连接...")
        await realtime_data.close()
        
    except Exception as e:
        print(f"出错: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_predict_signal()) 