# 策略模块 (Strategy)

本模块是量化交易系统的核心大脑，负责AI驱动的交易策略实现、智能投资组合管理、止盈止损控制和交易信号生成。采用模块化设计，便于扩展不同的交易策略，同时保持与其他模块的解耦合。

## 🧩 模块组成

### 核心组件

1. **LSTMModelTrainer** (`train.py`)
   - LSTM深度学习模型训练和预测
   - 支持多特征输入（价格、成交量、技术指标）
   - 自动模型保存和加载机制

2. **SignalGenerator** (`signals.py`)
   - 基于AI预测生成交易信号
   - 多技术指标确认机制
   - 信号强度和置信度评估

3. **PortfolioManager** (`portfolio_manager.py`)
   - 智能投资组合配置和再平衡
   - 多种配置策略（等权重、信号强度加权等）
   - 动态风险控制和仓位优化

4. **ProfitStopManager** (`profit_stop_manager.py`)
   - 智能止盈止损管理
   - 追踪止盈和固定止损
   - 日内风险控制

## 🚀 核心功能

### 1. AI驱动价格预测

```python
# LSTM模型训练和预测
model_trainer = LSTMModelTrainer(config_loader, historical_data_loader)
await model_trainer.train_model(symbols=["700.HK"], force_retrain=False)
prediction = await model_trainer.predict_next(symbol="700.HK")
```

**特性**：
- 90天历史数据训练
- 多特征融合（价格、成交量、RSI、MACD、布林带等）
- 128-64-32层LSTM架构
- Dropout防过拟合

### 2. 智能信号生成

```python
# 信号生成与回调注册
signal_gen = SignalGenerator(config_loader, realtime_data_manager, model_trainer)
signal_gen.register_signal_callback(callback_function)
signal = await signal_gen.predict_and_generate_signal(symbol="700.HK")
```

**特性**：
- 买入阈值4%，卖出阈值-4%
- 置信度阈值15%
- 多技术指标确认
- 信号强度自适应调整

### 3. 投资组合智能管理

```python
# 投资组合管理
portfolio_mgr = PortfolioManager(config, order_manager, realtime_mgr)
await portfolio_mgr.analyze_and_rebalance()
action, quantity = portfolio_mgr.get_position_suggestion(symbol, confidence)
```

**功能**：
- **配置策略**：信号强度加权配置
- **风险控制**：单仓位最大8%，最小5%
- **再平衡**：偏离度超过20%时自动再平衡
- **现金管理**：保留15%现金储备

### 4. 止盈止损管理

```python
# 止盈止损管理
profit_stop_mgr = ProfitStopManager(config, order_manager)
await profit_stop_mgr.monitor_positions()
exit_signals = await profit_stop_mgr.check_exit_signals()
```

**策略**：
- **固定止盈**：15%全部卖出
- **部分止盈**：8%卖出50%仓位
- **追踪止盈**：从最高点回调1%触发
- **固定止损**：8%亏损止损
- **追踪止损**：3%追踪止损
- **紧急止损**：15%亏损紧急清仓
- **日亏损限制**：单日亏损5%停止交易

## ⚙️ 策略配置

### 当前优化配置

```yaml
strategy:
  lookback_period: 90              # 回溯周期90天
  signal_interval: 600             # 信号间隔10分钟
  
  signal_processing:
    buy_threshold: 0.04            # 买入阈值4%
    sell_threshold: -0.04          # 卖出阈值-4%
    confidence_threshold: 0.15     # 置信度阈值15%
    min_signal_strength: 0.02      # 最小信号强度2%
    signal_decay_factor: 0.95      # 信号衰减因子
    trend_confirmation: true       # 趋势确认
    volume_confirmation: true      # 成交量确认
    
  risk_management:
    max_positions: 8               # 最大持仓数8个
    correlation_limit: 0.7         # 相关性限制70%
    volatility_filter: 0.25        # 波动率过滤25%
    sector_limit: 0.4              # 行业限制40%
    
  training:
    epochs: 200                    # 训练轮数200
    batch_size: 64                 # 批次大小64
    features:                      # 特征列表
      - close                      # 收盘价
      - volume                     # 成交量
      - high                       # 最高价
      - low                        # 最低价
      - turnover                   # 成交额
      - rsi                        # RSI指标
      - macd                       # MACD指标
      - bollinger_bands            # 布林带
      - sma_20                     # 20日均线
      - ema_12                     # 12日指数均线
```

### 投资组合配置

```yaml
portfolio:
  allocation_strategy: signal_strength_weight  # 信号强度加权
  max_position_weight: 0.08       # 单仓位最大权重8%
  min_position_weight: 0.05       # 单仓位最小权重5%
  rebalance_threshold: 0.2        # 再平衡阈值20%
  cash_reserve_ratio: 0.15        # 现金储备15%
  rebalance_frequency: 1800       # 再平衡频率30分钟
  signal_weight_factor: 2.0       # 信号权重因子
```

### 止盈止损配置

```yaml
execution:
  profit_taking:
    enable: true
    fixed_profit_pct: 15.0         # 固定止盈15%
    partial_profit_pct: 8.0        # 部分止盈8%
    trailing_profit_pct: 5.0       # 追踪止盈5%
    trailing_profit_step: 1.0      # 追踪步长1%
    
  stop_loss:
    enable: true
    fixed_stop_pct: 8.0            # 固定止损8%
    trailing_stop_pct: 3.0         # 追踪止损3%
    emergency_stop_pct: 15.0       # 紧急止损15%
    max_loss_per_day: 5.0          # 日最大亏损5%
```

## 🎯 接口设计

### 统一回调机制

```python
# 信号生成回调
async def on_signal_generated(signal):
    await order_manager.process_signal(signal)

signal_generator.register_signal_callback(on_signal_generated)

# 止盈止损回调
async def on_exit_signal(exit_signal):
    await order_manager.execute_exit(exit_signal)

profit_stop_manager.register_exit_callback(on_exit_signal)
```

### 依赖注入设计

```python
# 模块间解耦，通过构造函数注入依赖
class SignalGenerator:
    def __init__(self, config, realtime_mgr, model_trainer, portfolio_mgr=None):
        self.config = config
        self.realtime_mgr = realtime_mgr
        self.model_trainer = model_trainer
        self.portfolio_mgr = portfolio_mgr
```

## 📊 策略性能

### 回测表现

| 指标 | 优化前 | 优化后 | 改进 |
|------|--------|--------|------|
| 胜率 | 16.3% | 35%+ | +115% |
| 夏普比率 | -0.42 | 1.2+ | +385% |
| 最大回撤 | 15% | <10% | -33% |
| 信号活跃度 | 5% | 25% | +400% |

### 风险指标

- **Position Sizing**：严格控制单笔交易≤2%总资金
- **相关性控制**：避免高度相关股票集中持仓
- **波动率过滤**：过滤高波动率股票
- **日内风控**：单日亏损5%停止交易

## 🔧 策略扩展

### 添加新的交易策略

```python
class MomentumStrategy:
    def __init__(self, config):
        self.config = config
        
    async def predict(self, symbol, data):
        # 实现动量策略逻辑
        momentum_score = self._calculate_momentum(data)
        confidence = self._calculate_confidence(momentum_score)
        return {
            'prediction': momentum_score,
            'confidence': confidence,
            'signal': self._generate_signal(momentum_score)
        }
        
    def _calculate_momentum(self, data):
        # 动量计算逻辑
        return momentum_value
```

### 添加新的技术指标

```python
def custom_indicator(df, period=14):
    """自定义技术指标"""
    # 实现指标计算逻辑
    return indicator_values

# 在特征配置中添加
features:
  - custom_indicator
```

### 自定义配置策略

```python
class CustomAllocationStrategy:
    def calculate_weights(self, signals, positions):
        # 实现自定义配置逻辑
        return weights
        
# 在投资组合管理器中注册
portfolio_manager.register_strategy('custom', CustomAllocationStrategy())
```

## 🛠️ 开发工具

### 策略回测工具

```python
# 策略回测
python strategy_backtest.py --strategy lstm --start-date 2024-01-01 --end-date 2024-12-01

# 策略优化
python strategy_optimization.py --optimize-thresholds --optimize-features

# 性能分析
python strategy_performance.py --analyze-signals --analyze-returns
```

### 调试工具

```python
# 信号分析
signal_analyzer = SignalAnalyzer(config)
analysis = signal_analyzer.analyze_signal_quality(signals)

# 持仓分析
portfolio_analyzer = PortfolioAnalyzer(config)
analysis = portfolio_analyzer.analyze_portfolio_performance(positions)
```

## 📈 未来优化方向

### 1. 多因子模型
- 基本面因子（PE、PB、ROE等）
- 技术面因子（动量、反转、波动率）
- 市场微观结构因子（订单流、价差）

### 2. 机器学习增强
- 集成学习（Random Forest、XGBoost）
- 强化学习（Q-Learning、Actor-Critic）
- 时间序列分析（ARIMA-GARCH、Prophet）

### 3. 风险模型升级
- VaR和CVaR风险度量
- 压力测试和情景分析
- 动态对冲策略

### 4. 高频策略
- 微秒级延迟优化
- 市场微观结构建模
- 统计套利策略

## ⚠️ 注意事项

1. **模型过拟合**：定期重新训练模型，使用交叉验证
2. **数据质量**：确保历史数据的准确性和完整性
3. **市场变化**：策略参数需要根据市场环境调整
4. **风险控制**：始终将风险控制放在首位
5. **合规要求**：遵守相关监管规定和交易规则

## 📞 技术支持

- **模型训练问题**：检查数据质量和特征工程
- **信号质量差**：调整阈值参数和技术指标
- **风险控制异常**：检查风控参数和持仓状态
- **性能问题**：优化模型架构和计算效率

详细日志位于 `logs/strategy.log`，包含完整的策略执行轨迹。 