# 策略模块 (Strategy)

本模块负责交易策略的实现、模型训练和交易信号生成。设计目标是提供灵活的策略框架，便于扩展不同的交易策略，同时保持与其他模块的解耦合。

## 模块组成

本模块由以下主要组件组成：

1. **LSTMModelTrainer** (train.py)：负责LSTM模型的训练和预测
2. **SignalGenerator** (signals.py)：负责生成交易信号

## 接口设计

为保持模块间的解耦合，策略模块通过以下方式向其他模块提供服务：

### LSTMModelTrainer

```python
# 初始化
model_trainer = LSTMModelTrainer(config_loader, historical_data_loader)

# 训练模型
await model_trainer.train_model(symbols=["700.HK"], force_retrain=False)

# 预测下一时间点的价格
prediction = await model_trainer.predict_next(symbol="700.HK")

# 获取训练好的模型
model = model_trainer.get_model(symbol="700.HK")

# 保存模型
model_trainer.save_model(symbol="700.HK")

# 加载模型
model_trainer.load_model(symbol="700.HK")
```

### SignalGenerator

```python
# 初始化
signal_gen = SignalGenerator(config_loader, realtime_data_manager, model_trainer)

# 注册信号回调
signal_gen.register_signal_callback(callback_function)

# 生成信号
signal = await signal_gen.predict_and_generate_signal(symbol="700.HK")

# 获取最新信号
signal = signal_gen.get_latest_signal(symbol="700.HK")

# 开始定时生成信号
asyncio.create_task(signal_gen.scheduled_signal_generation(interval_seconds=60))
```

## 解耦合设计

本模块遵循以下解耦合原则：

1. **依赖注入**：通过构造函数接收其他模块的实例，而非直接创建它们
2. **回调机制**：通过回调函数向外部模块通知信号生成事件
3. **接口一致性**：提供统一的预测和信号生成接口，隐藏底层实现细节
4. **封装算法逻辑**：策略算法完全封装在模块内部，外部只需关注结果

## 与其他模块交互

策略模块与其他模块的交互通过以下机制：

1. **数据获取**：从数据加载模块获取历史和实时数据
2. **信号传递**：生成交易信号后通过回调通知订单执行模块
3. **配置读取**：从配置加载器读取策略参数
4. **模型存储**：将训练好的模型保存到磁盘供后续使用

示例：
```python
# 订单执行模块注册回调
async def on_signal_generated(signal):
    # 处理信号
    await order_manager.process_signal(signal)

# 信号生成器注册回调
signal_generator.register_signal_callback(on_signal_generated)
```

## 策略参数

```yaml
strategy:
  lstm:
    lookback_days: 30  # 训练时使用的历史天数
    prediction_horizon: 1  # 预测未来天数
    epochs: 100  # 训练轮数
    batch_size: 32  # 批次大小
    dropout_rate: 0.2  # Dropout比率
    hidden_units: 50  # 隐藏层单元数
    
  signals:
    thresholds:
      buy: 0.5  # 买入信号阈值（百分比）
      sell: -0.5  # 卖出信号阈值（百分比）
    confidence_multiplier: 2.0  # 交易量与置信度的乘数
    min_signal_interval: 300  # 最小信号间隔（秒）
```

## 扩展策略

要添加新的交易策略，可以：

1. 创建新的策略类，实现 `predict` 方法
2. 在 `SignalGenerator` 中注册新策略
3. 在配置文件中添加相应参数

```python
class MyNewStrategy:
    def __init__(self, config):
        self.config = config
        # 初始化策略
        
    async def predict(self, symbol, data):
        # 实现预测逻辑
        return prediction_result
```

## 依赖

- TensorFlow/Keras
- pandas
- numpy
- scikit-learn

## 注意事项

1. 模型训练可能耗费大量计算资源，建议使用生产环境前预先训练好模型
2. 初次使用时训练模型可能需要较长时间，后续可以通过 `force_retrain=False` 加载已有模型
3. 实时信号生成依赖于实时数据的可用性，确保数据加载模块正常工作
4. 过于频繁的信号生成可能导致过度交易，请合理设置 `min_signal_interval` 