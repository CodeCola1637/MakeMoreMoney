# 智能量化交易系统 v4.0

> 基于 LongBridge OpenAPI 的模块化量化交易系统，覆盖港股与美股（含做空、盘前盘后）。
> 采用 **触发驱动 + 辅助确认** 架构：Volume Anomaly / SEC / CCASS 三类触发策略 + LSTM / Technical / Breakout 三类辅助策略。

---

## 🎯 核心特性

- **多触发源**：Volume Anomaly（实时大宗交易）+ SEC 13F/Form 4（机构持仓）+ CCASS（港股托管行持仓变化）
- **多策略融合**：三辅助策略置信度加权投票，弃权机制自动重新分配权重
- **多市场分工**：SEC 主服务美股、CCASS 主服务港股、Volume Anomaly 全市场通用
- **完善的风控体系**：FundGuard（资金/杠杆/集中度）+ SignalFilter（冷却/护栏/反转）+ ProfitStopMgr（多档止盈止损）
- **保证金主动监控**：实时跟踪 broker `risk_level` / `leverage`，超阈值自动拦截买入
- **做空支持**：仅美股启用 SELL→SHORT 自动转换
- **扩展交易时段**：美股盘前 04:00-09:30、盘后 16:00-20:00
- **TaskManager**：统一异步任务调度，自动重启 + 致命错误熔断
- **Web 监控面板**：异常信号、订单流水、机构动向、账户状态实时可视化

---

## 🏗️ 项目结构

```
MakeMoreMoney/
├── main.py                       # 主程序入口（构建 TradingContext + 注册任务）
├── config.yaml                   # 全局配置（敏感字段使用 ${ENV} 引用）
├── .env                          # API 密钥（不入版本库）
├── requirements.txt
│
├── data_loader/                  # 🔄 数据层
│   ├── realtime.py               #   实时行情订阅 / 推送分发
│   └── historical.py             #   历史 K 线获取与缓存
│
├── strategy/                     # 🧠 策略层
│   ├── signals.py                #   Signal 数据类 + LSTM 信号生成
│   ├── strategy_ensemble.py      #   多策略加权组合器
│   ├── volume_anomaly_detector.py#   实时异常成交量检测（触发）
│   ├── volume_strategy.py        #   Volume Anomaly 策略包装
│   ├── sec_strategy.py           #   SEC 13F/Form 4 策略（触发, 美股）
│   ├── institutional_tracker.py  #   SEC EDGAR 机构数据采集
│   ├── ccass_strategy.py         #   CCASS 港股持仓策略（触发, 港股）
│   ├── ccass_tracker.py          #   HKEX CCASS 数据采集
│   ├── technical_strategy.py     #   技术指标策略（辅助）
│   ├── breakout_strategy.py      #   通道突破策略（辅助）
│   ├── attention_lstm.py         #   Attention-LSTM 模型
│   ├── feature_engineer.py       #   特征工程
│   ├── data_normalizer.py        #   数据归一化
│   ├── train.py                  #   LSTM 训练入口
│   ├── signal_filter.py          #   信号过滤（冷却/护栏/反转/置信度覆写）
│   ├── correlation_filter.py     #   资产相关性过滤
│   ├── profit_stop_manager.py    #   止盈止损管理
│   ├── portfolio_manager.py      #   投资组合状态同步
│   └── stock_discovery.py        #   全市场扫描发现机会
│
├── execution/                    # ⚡ 执行层
│   ├── order_manager.py          #   订单门面（OrderManager facade）
│   ├── order_executor.py         #   订单提交 / 取消
│   ├── order_tracker.py          #   订单状态追踪 + CSV 原子更新
│   ├── order_validator.py        #   订单预验证（fail-closed 模式）
│   ├── trade_executor.py         #   底层交易执行
│   ├── pending_order_manager.py  #   挂单管理
│   ├── position_service.py       #   持仓 / 余额 / 保证金查询（带 TTL 缓存）
│   ├── fund_guard.py             #   资金 + 杠杆 + 集中度风控
│   └── task_manager.py           #   异步任务调度器
│
├── tasks/                        # ⏱️ 周期任务
│   ├── trading_context.py        #   TradingContext 数据类（依赖注入）
│   ├── signal_tasks.py           #   信号生成 / Volume Anomaly 任务
│   ├── monitoring_tasks.py       #   止盈止损 / 健康检查 / 组合更新
│   └── discovery_tasks.py        #   股票发现 / SEC / CCASS 扫描
│
├── monitoring/                   # 📊 系统监控
│   ├── health_check.py
│   ├── memory_manager.py
│   ├── cache_manager.py
│   └── data_quality.py
│
├── web/                          # 🌐 Web 面板
│   └── dashboard.py              # Flask 仪表板（默认端口 8888）
│
├── databases/                    # 💾 SQLite 持久化
├── logs/                         # 📜 运行日志 / 订单 CSV
├── models/                       # 🤖 LSTM 模型权重
├── data_cache/                   # 📁 历史数据缓存
├── utils.py                      # 🛠️ ConfigLoader / 日志 / 环境变量
│
├── simulate_optimizations.py     # 优化效果模拟脚本
├── simulate_trailing_stop_long.py# 追踪止损长周期回测脚本
│
└── docs/
    ├── 交易策略说明文档.md         # 📖 详细策略说明（v4.0）
    └── archive/                   # 历史报告归档（优化计划 / 验证报告等）
```

---

## 🧠 核心策略一览

| 类别 | 策略 | 角色 | 权重 | 适用市场 |
|------|------|------|------|---------|
| 触发 | Volume Anomaly | 实时检测大宗/激增/突刺/量价背离 | 30% | 全部 |
| 触发 | SEC | 跟踪 13F + Form 4 大型机构动向 | 20% | 主要美股 |
| 触发 | CCASS | 港股 HKEX 中央结算持仓变化 | 20% | 仅港股 |
| 辅助 | LSTM | 90 日回看深度学习预测 | 10% | 全部 |
| 辅助 | Technical | RSI / MACD / 布林带 / 均线 | 10% | 全部 |
| 辅助 | Breakout | N 日通道突破 + ATR 余量 | 10% | 全部 |

> **触发策略**才能发起交易；辅助策略只投票确认方向。所有策略支持「无数据弃权」，权重自动重新分配。

---

## 🛡️ 三层风控体系

### 事前风控
- **OrderValidator**：市场时间、价格合理性、最小手数（fail-closed）
- **CorrelationFilter**：资产相关性 > 0.7 拦截
- **SignalFilter**：置信度阈值（含 CCASS 0.05 触发覆写）、冷却期、价格变化、港股开盘 10min 噪声过滤、SELL→BUY 回补价护栏

### 事中风控
- **FundGuard**
  - 单笔限制（5% 总权益）/ 总仓位（80%）/ **单标的集中度（20%）**
  - **杠杆上限 2.5x**，超出拒绝买入
  - **保证金缓冲监控**：< 35% 预警，< 25% 危险
  - 真实净资产以 broker `net_assets` 为准，避免双重计算

### 事后风控
- **ProfitStopManager**
  - 固定止盈 +15%、部分止盈 +8%（平仓 50%）
  - 追踪止盈：盈利 ≥ +5% 后步进 1.5%
  - 固定止损 -5%、紧急止损 -10%、追踪止损 -4%
  - 单日亏损限制 3%，触发后暂停交易
  - 止损退出 → 通知 SignalFilter 进入 1h 再入场冷却
  - 重试容错（保留 retry_count，市场开盘自动复位）

---

## ⏱️ 后台任务调度

| 任务 | 间隔 | 说明 |
|------|------|------|
| `signal_generation` | 600s | Ensemble 全标的轮询 |
| `volume_anomaly` | 60s | 异常成交量队列消费 + 即时 Ensemble |
| `ccass_tracking` | 7200s | HKEX CCASS 扫描（港股） |
| `institutional_tracking` | 7200s | SEC 13F/Form 4 扫描 |
| `profit_stop_monitor` | 30s | 持仓盈亏与止盈止损 |
| `portfolio_update` | 300s | 持仓 / 市值同步 |
| `health_check` | 300s | 系统健康 + 保证金风险 |
| `stock_discovery` | 3600s | 全市场扫描发现机会 |

---

## 🔧 安装与启动

### 环境要求
- Python 3.9+
- TensorFlow 2.10+（用于 LSTM）
- LongBridge Python SDK
- Flask（Web 面板）

### 步骤

```bash
# 1) 安装依赖
pip install -r requirements.txt

# 2) 配置 API 密钥（敏感字段从 .env 读取）
cp .env.example .env
vi .env   # 填入 LONGPORT_APP_KEY / APP_SECRET / ACCESS_TOKEN

# 3) 编辑业务配置
vi config.yaml

# 4) 首次启动 — 训练 LSTM 模型
python main.py --train --symbols "700.HK 9988.HK AAPL.US"

# 5) 正常启动（默认读取 config.yaml 中 quote.symbols）
python main.py

# 或启动时显式覆盖标的池
python main.py --symbols "700.HK 9988.HK 388.HK 941.HK 9992.HK \
                         AAPL.US GOOGL.US MSFT.US NVDA.US TSLA.US MU.US SNDK.US"
```

### 启动选项

| 参数 | 说明 |
|------|------|
| `--symbols` | 覆盖配置文件中的标的池（空格分隔） |
| `--train`   | 训练 LSTM 模型后退出 |
| `--mock`    | Mock 模式（不真实下单） |
| `--no-dashboard` | 不启动 Web 面板 |

---

## 📊 监控与运维

### 日志
```bash
tail -f trading_output.log                    # 完整运行日志
grep "组合信号\|BUY\|SELL\|SHORT" trading_output.log
grep "TaskManager\|健康检查" trading_output.log
```

### 订单
```bash
tail -20 logs/orders.csv                      # 实时订单状态（已修复 NotReported bug）
```

### Web 面板
- 默认地址：`http://localhost:8888`
- 包含：异常信号 / 订单流水 / 机构动向 / 账户状态
- ⚠️ 默认 `0.0.0.0` 监听，公网部署请加反向代理认证

### Longbridge MCP（可选）
- 已配置 `.cursor/mcp.json`，可在 Cursor 中通过自然语言查询账户、行情、订单

---

## ⚙️ 配置示例

```yaml
strategy:
  lookback_period: 90
  signal_interval: 600
  signal_processing:
    confidence_threshold: 0.08
    trigger_confidence_overrides:
      ccass: 0.05            # CCASS 弱信号单独阈值

  cover_price_guard:
    enable: true
    lookback_hours: 24
    max_price_premium_pct: 0.0   # 24h 内 BUY 价不得高于最近 SELL 价

  signal_filter:
    hk_open_filter_minutes: 10   # 港股开盘 10 分钟过滤 BUY

ensemble:
  method: confidence_weight
  strategy_weights:
    volume_anomaly: 0.30
    sec: 0.20
    ccass: 0.20
    lstm: 0.10
    technical: 0.10
    breakout: 0.10

execution:
  position_pct: 5.0              # 单笔 5% 总权益
  risk_control:
    daily_loss_pct: 3.0
    max_total_position_pct: 80.0
    max_single_position_pct: 20.0
    max_leverage: 2.5
    margin_warning_pct: 35.0
    margin_danger_pct: 25.0
  allow_sell_to_short: true      # 仅美股生效
```

完整参数请见 `config.yaml`。

---

## 📚 文档导航

| 文档 | 说明 |
|------|------|
| [`docs/交易策略说明文档.md`](docs/交易策略说明文档.md) | 策略与风控的完整业务逻辑说明（v4.0） |
| [`strategy/README.md`](strategy/README.md) | 策略模块开发说明 |
| [`execution/README.md`](execution/README.md) | 执行模块开发说明 |
| [`data_loader/README.md`](data_loader/README.md) | 数据加载模块开发说明 |
| [`databases/README.md`](databases/README.md) | 持久化与表结构 |
| `docs/archive/` | 历史阶段性优化报告与诊断（按需查阅） |

---

## 🧪 模拟与回测

仓库内附带两个轻量回测脚本，便于验证参数调整：

```bash
# 模拟：CCASS 阈值放宽 + 回补价护栏 对历史交易的效果
python simulate_optimizations.py

# 回测：港股 trailing_stop_pct 从 4% 调到 5% 的长周期效果
python simulate_trailing_stop_long.py
```

> 当前尚未提供完整回测框架（计划中）。重大策略改动建议先用模拟脚本验证再上线。

---

## 📈 版本历史

### v4.0（2026-04）
- ✅ **CCASS 港股持仓信号**：HKEX 中央结算数据接入，弥补 SEC 在港股盲区
- ✅ **多空分歧 = HOLD**：Volume Anomaly 在多空均衡时主动弃权
- ✅ **港股开盘 10min 噪声过滤**：抑制集合竞价段虚假信号
- ✅ **单标的集中度上限**：`max_single_position_pct: 20%`
- ✅ **保证金主动保护**：监控 broker `risk_level` / `leverage`，超阈值拦截买入
- ✅ **触发策略阈值覆写**：CCASS 等弱信号策略单独配置 confidence 阈值
- ✅ **SELL→BUY 回补价护栏**：24h 内 BUY 价不得高于最近 SELL 价

### v3.5（2026-03）
- ✅ **Breakout 通道突破策略**（辅助）
- ✅ **订单状态 CSV 原子更新**：修复全部 `NotReported` bug
- ✅ **止盈止损订单容错**：保留 retry_count，避免无限重试
- ✅ **TOCTOU 竞态修复**：信号原子检查 + 记录，消除「卖出后立即买入」
- ✅ **做空仅限美股**：港股 SELL 不再自动转 SHORT
- ✅ **追踪止损/止盈参数放宽**：4% 止损 + 1.5% 步进，匹配波动性

### v3.0（2026-01）
- ✅ **TaskManager** 重构异步任务管理
- ✅ **SEC 自动扩列**：13F/Form 4 大额标的自动加入观察列表
- ✅ **触发驱动架构**：仅 Volume Anomaly + SEC 可触发交易
- ✅ **股票发现模块**、**做空交易支持**、**美股盘前盘后**
- ✅ **关联性过滤接入执行路径**

### v2.0
- 投资组合管理 + 止盈止损
- 多策略组合器、技术指标策略

### v1.0
- 基础交易系统、LSTM 预测模型、风险控制框架

---

## ⚠️ 重要提醒

- **仅供学习研究**：实盘交易需谨慎评估风险
- **API 密钥安全**：必须使用 `.env`，切勿入库
- **资金安全**：建议先用 Mock 模式或模拟账户测试
- **保证金风险**：开启 `allow_sell_to_short` 或杠杆交易后，请监控保证金缓冲
- **Web 面板鉴权**：公网部署务必加 IP 白名单或反向代理认证
- **合规交易**：遵守相关市场（HKEX / SEC / FINRA）交易规则

---

## 📄 许可证

MIT License
