# 智能量化交易系统

基于 LongBridge API 的模块化量化交易系统，集成了 LSTM 模型进行价格预测、智能投资组合优化和全面风险控制，支持实时行情订阅、交易信号生成和自动化交易执行。

## 🚀 系统特点

- **模块化设计**：系统分为多个功能独立的模块，便于维护和扩展
- **智能投资组合优化**：每次信号生成后自动分析和优化当前持仓配置
- **多策略融合**：LSTM深度学习 + 技术指标 + 投资组合管理
- **实时行情订阅**：通过 LongBridge API 订阅港股、美股等市场的实时行情
- **AI驱动决策**：使用深度学习模型对未来价格走势进行预测
- **智能风险控制**：多层次风险控制机制，包括单笔限制、日亏损限制、止盈止损
- **订单自动执行**：根据交易信号自动下单并跟踪订单状态
- **成本效益优化**：内置交易成本分析，确保每笔交易具有盈利潜力

## 🏗️ 系统架构

系统采用解耦合设计，各模块通过清晰的接口交互：

```
├── data_loader/       # 🔄 数据管理模块
│   ├── realtime.py    # 实时行情订阅与处理
│   └── historical.py  # 历史K线数据获取与缓存
├── strategy/          # 🧠 策略引擎
│   ├── train.py       # LSTM模型训练与预测
│   ├── signals.py     # 交易信号生成
│   ├── portfolio_manager.py    # 投资组合管理
│   └── profit_stop_manager.py  # 止盈止损管理
├── execution/         # ⚡ 交易执行
│   └── order_manager.py # 订单管理、智能优化与执行
├── databases/         # 💾 数据存储
├── logs/             # 📊 日志记录
├── models/           # 🤖 AI模型存储
├── utils.py          # 🛠️ 工具类
├── config.yaml       # ⚙️ 配置文件
└── main.py           # 🎯 主程序入口
```

## 🎯 核心功能

### 1. 智能投资组合优化
每次收到交易信号后，系统会自动执行全面的投资组合分析：

- **挂单智能清理**：6维度分析不合理挂单（价格偏离、订单年龄、资金占用等）
- **持仓动态优化**：清理小仓位、减少过度集中持仓
- **资金配置优化**：根据信号强度和风险控制动态调整仓位
- **成本效益分析**：确保每笔交易具有盈利潜力

### 2. 多层风险控制
- **Position Sizing**：单笔交易不超过账户资金的2%
- **日亏损控制**：日亏损达到3%时自动停止交易
- **止盈止损**：支持固定止盈(15%)、部分止盈(8%)、追踪止损(3%)
- **相关性控制**：避免高度相关股票过度集中
- **波动率过滤**：过滤高波动率股票降低风险

### 3. AI驱动策略
- **LSTM深度学习**：使用90天历史数据训练，预测价格变动
- **技术指标融合**：RSI、MACD、布林带、SMA、EMA多指标确认
- **信号强度评估**：置信度阈值15%，确保信号质量
- **趋势确认**：多重技术指标确认避免假信号

### 4. 成本效益优化
- **交易成本分析**：精确计算各市场手续费（美股0.5%+$0.99，港股0.25%+印花税）
- **最小交易金额**：设置$200最小交易额，避免过小交易
- **成本占比控制**：交易成本不超过2%(常规)或3%(小额)
- **预期收益评估**：基于信号置信度评估预期收益

## 📊 策略配置

当前优化后的策略参数：

```yaml
strategy:
  lookback_period: 90        # 回溯周期延长到90天
  signal_interval: 600       # 信号生成间隔10分钟
  signal_processing:
    buy_threshold: 0.04      # 买入阈值4%
    sell_threshold: -0.04    # 卖出阈值-4%
    confidence_threshold: 0.15 # 最低置信度15%
  risk_management:
    max_positions: 8         # 最大持仓数8个
    correlation_limit: 0.7   # 相关性限制70%
    volatility_filter: 0.25  # 波动率过滤25%
```

## 🔧 安装与配置

### 1. 环境要求
- Python 3.8+
- LongBridge SDK
- TensorFlow 2.10+

### 2. 快速启动
```bash
# 克隆项目
git clone <repository-url>
cd MakeMoreMoney

# 安装依赖
pip install -r requirements.txt

# 配置API密钥
cp .env.example .env
# 编辑.env文件填入API密钥

# 启动系统
python main.py --train  # 首次启动训练模型
```

### 3. 便捷启动脚本
```bash
# 使用启动脚本
chmod +x start.sh
./start.sh --symbols "700.HK 9988.HK AAPL.US" --train
```

## 📈 性能表现

### 历史回测结果
- **总体胜率**：从16.3%优化到预期35%+
- **风险控制**：最大回撤控制在10%以内
- **夏普比率**：从-0.42优化到预期1.2+
- **交易效率**：大幅减少无效交易，提高资金使用效率

### 实时监控指标
- 实时盈亏跟踪
- 持仓风险评估
- 交易成本分析
- 信号质量监控

## 🛡️ 风险管理

### 多层次风险控制
1. **事前风控**：信号质量过滤、成本效益分析
2. **事中风控**：Position Sizing、相关性控制
3. **事后风控**：止盈止损、日亏损限制

### 异常处理
- API连接异常自动重连
- 订单执行失败自动重试
- 数据异常智能过滤
- 系统异常优雅降级

## 📝 使用说明

### 基本命令
```bash
# 启动交易系统
python main.py

# 重新训练模型
python main.py --train

# 指定交易标的
python main.py --symbols "AAPL.US TSLA.US"

# 自定义配置
python main.py --config custom_config.yaml
```

### 监控命令
```bash
# 查看实时日志
tail -f logs/trading.log

# 分析交易记录
python analyze_trading_performance.py

# 系统诊断
python diagnose_system.py
```

## 🔄 系统升级历程

### 最新版本特性
- ✅ **智能投资组合优化**：全自动分析和优化持仓配置
- ✅ **策略参数优化**：基于历史表现优化买卖阈值
- ✅ **成本效益分析**：精确的交易成本计算和效益评估
- ✅ **多技术指标融合**：RSI、MACD、布林带等多指标确认
- ✅ **风险控制增强**：相关性控制、波动率过滤、止盈止损

### 历史版本
- v2.0: 添加投资组合管理和止盈止损
- v1.5: 优化LSTM模型和信号生成
- v1.0: 基础交易系统和风险控制

## 🚀 扩展开发

### 添加新策略
```python
class CustomStrategy:
    def predict(self, symbol, data):
        # 实现自定义策略逻辑
        return prediction_result
```

### 自定义风控规则
```python
def custom_risk_check(signal, positions, balance):
    # 实现自定义风控逻辑
    return is_allowed, reason
```

## ⚠️ 重要提醒

- **仅供学习研究**：本系统仅用于学习和研究，实盘交易需谨慎评估风险
- **API密钥安全**：请妥善保管API密钥，避免泄露
- **资金安全**：建议先用少量资金测试，确认系统稳定后再增加投入
- **合规交易**：请遵守相关市场的交易规则和法规

## 📞 技术支持

- 查看详细日志：`logs/trading.log`
- 系统诊断工具：`diagnose_system.py`
- 性能分析工具：`analyze_trading_performance.py`
- 配置优化工具：`strategy_optimization.py`

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件 