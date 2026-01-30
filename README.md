# 智能量化交易系统 v3.0

基于 LongBridge API 的模块化量化交易系统，集成了 LSTM 深度学习模型、多策略组合、智能投资组合优化、股票发现和全面风险控制。支持港股和美股（含做空、盘前盘后交易）的实时行情订阅和自动化交易执行。

## 🚀 系统特点

- **模块化架构**：TaskManager 统一管理异步任务，便于维护和扩展
- **多策略融合**：LSTM深度学习 + 技术指标策略，置信度加权组合
- **股票发现**：自动扫描市场发现潜在买入机会（RSI超卖、MACD金叉等）
- **做空支持**：美股完整做空交易支持（开空、平空）
- **扩展交易时段**：美股盘前(4:00-9:30)和盘后(16:00-20:00)交易
- **智能风险控制**：多层次风控（资金守卫、信号过滤、相关性控制）
- **实时行情订阅**：通过 LongBridge API 订阅港股、美股实时行情
- **成本效益优化**：内置交易成本分析，确保每笔交易具有盈利潜力

## 🏗️ 系统架构

```
├── data_loader/              # 🔄 数据管理模块
│   ├── realtime.py           # 实时行情订阅与处理
│   └── historical.py         # 历史K线数据获取与缓存
├── strategy/                 # 🧠 策略引擎
│   ├── signals.py            # 信号类型定义与LSTM信号生成
│   ├── technical_strategy.py # 技术指标策略
│   ├── strategy_ensemble.py  # 多策略组合器
│   ├── stock_discovery.py    # 股票发现模块 [NEW]
│   ├── signal_filter.py      # 信号过滤器
│   ├── correlation_filter.py # 相关性过滤器
│   ├── data_normalizer.py    # 数据归一化器
│   ├── feature_engineer.py   # 特征工程
│   ├── attention_lstm.py     # Attention-LSTM模型
│   ├── portfolio_manager.py  # 投资组合管理
│   ├── profit_stop_manager.py# 止盈止损管理
│   └── train.py              # 模型训练
├── execution/                # ⚡ 交易执行
│   ├── order_manager.py      # 订单管理（支持做空）
│   ├── order_validator.py    # 订单预验证器
│   ├── fund_guard.py         # 资金守卫
│   ├── task_manager.py       # 异步任务管理器
│   └── pending_order_manager.py # 挂单管理
├── monitoring/               # 📊 系统监控
│   ├── health_check.py       # 健康检查
│   ├── memory_manager.py     # 内存管理
│   ├── cache_manager.py      # 缓存管理
│   └── data_quality.py       # 数据质量监控
├── databases/                # 💾 数据存储
├── logs/                     # 📊 日志记录
├── models/                   # 🤖 AI模型存储
├── data_cache/               # 📁 数据缓存
├── utils.py                  # 🛠️ 工具类（ConfigLoader单例、统一日志）
├── config.yaml               # ⚙️ 配置文件
└── main.py                   # 🎯 主程序入口（TaskManager集成）
```

## 🎯 核心功能

### 1. 信号类型系统
支持完整的多空交易信号：

| 信号类型 | 说明 | 适用市场 |
|---------|------|----------|
| BUY | 买入（开多仓或平空仓） | 全部 |
| SELL | 卖出（平多仓） | 全部 |
| SHORT | 做空（开空仓） | 美股 |
| COVER | 平空（买入平仓空头） | 美股 |
| HOLD | 持有观望 | 全部 |

### 2. 股票发现模块
自动扫描市场寻找买入机会：

- **技术筛选条件**：
  - RSI 超卖（< 30）
  - MACD 金叉
  - 均线突破
  - 放量突破
  - 价格反转
  - 支撑位反弹

- **股票池**：港股20只 + 美股20只热门标的
- **观察列表**：最多20只，48小时过期
- **入场时机**：自动检测最佳入场点

### 3. 多策略组合器
```yaml
ensemble:
  method: confidence_weight  # 置信度加权
  strategies:
    - lstm                   # LSTM深度学习
    - technical              # 技术指标策略
  min_strategies_agreement: 1
  confidence_threshold: 0.02
```

### 4. 风险控制体系

#### 资金守卫 (FundGuard)
- 账户余额检查（禁止负余额交易）
- 最小储备金保护（$1000）
- 单笔交易限制（2%总权益）
- 日亏损限制（3%）
- 总仓位限制（80%）

#### 信号过滤器 (SignalFilter)
- 同股票信号冷却期（600秒）
- 每日信号上限（10个/股票）
- 价格变化阈值（1%）
- 最低置信度过滤（0.1）

#### 相关性过滤器 (CorrelationFilter)
- 资产相关性检查（限制 0.7）
- 防止过度集中

### 5. 美股扩展交易
```yaml
us_extended_hours:
  enable_pre_market: true     # 盘前 4:00-9:30 ET
  enable_after_hours: true    # 盘后 16:00-20:00 ET

# 做空配置
enable_short_selling: true
max_short_position: 200       # 单只最大空头
```

### 6. 任务管理器 (TaskManager)
统一管理所有异步任务：

| 任务 | 间隔 | 说明 |
|------|------|------|
| signal_generation | 600s | 策略信号生成 |
| portfolio_update | 300s | 投资组合更新 |
| profit_stop_monitor | 30s | 止盈止损监控 |
| health_check | 300s | 系统健康检查 |
| stock_discovery | 3600s | 股票发现扫描 |

## 📊 配置说明

### 策略配置
```yaml
strategy:
  lookback_period: 90           # LSTM回溯周期
  signal_interval: 600          # 信号间隔（秒）
  signal_processing:
    buy_threshold: 0.04         # 买入阈值
    sell_threshold: -0.04       # 卖出阈值
    confidence_threshold: 0.1   # 最低置信度
  risk_management:
    max_positions: 8            # 最大持仓数
    correlation_limit: 0.7      # 相关性限制
    volatility_filter: 0.3      # 波动率过滤
```

### 执行配置
```yaml
execution:
  min_trade_value: 200          # 最小交易金额
  max_position_size: 2000       # 最大持仓
  min_profit_threshold: 2.5     # 最小利润阈值(%)
  risk_control:
    daily_loss_pct: 3.0         # 日亏损限制
    position_pct: 2.0           # 单笔限制
    max_total_position_pct: 80  # 总仓位限制
```

## 🔧 安装与使用

### 环境要求
- Python 3.8+
- TensorFlow 2.10+
- LongBridge SDK

### 快速启动
```bash
# 安装依赖
pip install -r requirements.txt

# 配置API（编辑config.yaml）
vi config.yaml

# 首次启动（训练模型）
python main.py --train --symbols "700.HK 9988.HK AAPL.US"

# 正常启动
python main.py --symbols "700.HK 9988.HK 1299.HK 388.HK 941.HK AAPL.US GOOGL.US MSFT.US NVDA.US TSLA.US"
```

### 监控命令
```bash
# 实时日志
tail -f trading_output.log

# 查看信号
grep "组合信号\|BUY\|SELL\|SHORT" trading_output.log

# 查看订单
tail -20 logs/orders.csv

# 查看任务状态
grep "TaskManager" trading_output.log
```

## 📈 版本历史

### v3.0 (2026-01-30) - 当前版本
- ✅ **股票发现模块**：自动扫描市场发现买入机会
- ✅ **做空交易支持**：完整的美股做空功能（SHORT/COVER）
- ✅ **扩展交易时段**：美股盘前盘后交易
- ✅ **TaskManager**：统一异步任务管理
- ✅ **ConfigLoader单例**：避免配置重复加载
- ✅ **统一日志系统**：防止日志丢失
- ✅ **资金守卫**：集中式财务风控
- ✅ **信号过滤器**：防止过度交易
- ✅ **相关性过滤器**：投资组合风险分散
- ✅ **订单预验证器**：降低拒单率
- ✅ **LSTM模型修复**：5特征正确输入

### v2.0
- 添加投资组合管理和止盈止损
- 多策略组合器
- 技术指标策略

### v1.0
- 基础交易系统
- LSTM预测模型
- 风险控制框架

## 🛡️ 风险管理

### 三层风控体系
1. **事前风控**
   - 订单预验证（市场时间、资金、持仓）
   - 信号过滤（冷却期、置信度）
   - 成本效益分析

2. **事中风控**
   - Position Sizing（单笔2%限制）
   - 相关性控制（0.7上限）
   - 资金守卫检查

3. **事后风控**
   - 止盈止损（固定15%/追踪5%）
   - 日亏损限制（3%停止交易）
   - 挂单清理

## ⚠️ 重要提醒

- **仅供学习研究**：实盘交易需谨慎评估风险
- **API密钥安全**：妥善保管，避免泄露
- **资金安全**：建议先用模拟账户测试
- **合规交易**：遵守相关市场交易规则

## 📄 许可证

MIT License
