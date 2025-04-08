#!/usr/bin/env python
from strategy.signals import Signal, SignalType
import traceback

try:
    # 测试创建信号
    signal = Signal(
        symbol="700.HK",
        signal_type=SignalType.BUY,
        price=440.4,
        confidence=0.2,
        quantity=10,
        extra_data={"predicted_change_pct": 1.5, "model": "LSTM"}
    )
    print(f"成功创建信号: {signal}")
    print(f"signal_type类型: {type(signal.signal_type)}")
    
    # 测试字典转换
    signal_dict = signal.to_dict()
    print(f"信号字典: {signal_dict}")
    
except Exception as e:
    print(f"出错: {e}")
    traceback.print_exc()
