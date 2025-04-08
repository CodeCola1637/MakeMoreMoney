# 量化交易系统

基于 LongBridge API 的模块化量化交易系统，集成了 LSTM 模型进行价格预测，支持实时行情订阅、交易信号生成和自动化交易执行。系统采用解耦合设计，各模块之间通过清晰的接口交互。

## 系统特点

- **模块化设计**：系统分为多个功能独立的模块，便于维护和扩展
- **解耦合架构**：各模块通过接口交互，减少直接依赖
- **实时行情订阅**：通过 LongBridge API 订阅港股、美股等市场的实时行情
- **LSTM 模型预测**：使用深度学习模型对未来价格走势进行预测
- **交易信号生成**：基于预测结果和技术指标生成买入/卖出信号
- **订单自动执行**：根据交易信号自动下单并跟踪订单状态
- **风险控制**：内置风险控制机制，控制仓位大小和每日交易限额
- **实时监控**：详细的日志记录系统活动，方便监控系统运行状态

## 系统架构

系统由以下主要模块组成，每个模块负责特定功能并通过明确的接口与其他模块交互：

1. **数据加载模块** (`data_loader/`)：
   - 负责从 LongBridge API 获取实时和历史行情数据
   - 提供数据缓存和预处理功能
   - 通过回调机制向其他模块提供数据更新通知

2. **策略模块** (`strategy/`)：
   - 负责实现交易策略和信号生成逻辑
   - 包含 LSTM 模型的训练和预测功能
   - 生成买入/卖出信号并通过回调通知订单执行模块

3. **订单执行模块** (`execution/`)：
   - 负责将交易信号转换为实际订单
   - 实现风险控制和订单管理功能
   - 监控订单状态并提供执行回报

4. **数据库模块** (`databases/`)：
   - 负责持久化存储交易数据、订单记录和历史数据
   - 提供统一的数据访问接口
   - 支持查询和分析历史交易记录

5. **工具和配置模块** (`utils.py`, `config.yaml`)：
   - 提供通用工具函数和配置管理
   - 实现日志记录和环境变量管理

## 模块交互流程

系统的主要工作流程如下：

1. **数据流**：
   - 实时数据管理器订阅股票行情 → 信号生成器接收行情更新 → 生成交易信号 → 订单管理器执行订单
   - 历史数据加载器获取历史数据 → LSTM模型训练器训练模型 → 模型用于预测和信号生成

2. **控制流**：
   - 主程序初始化各模块 → 启动实时数据订阅 → 启动信号生成定时任务 → 处理交易信号和订单执行
   - 信号生成器生成信号 → 通过回调通知订单管理器 → 订单管理器执行风控检查 → 提交订单

3. **解耦合机制**：
   - **依赖注入**：各模块通过构造函数接收依赖项
   - **回调机制**：模块间通过回调函数传递事件和结果
   - **统一接口**：各模块提供清晰的公共接口
   - **配置集中**：使用统一的配置管理器

## 安装步骤

### 1. 环境要求

- Python 3.8 或更高版本
- LongBridge SDK
- 其他依赖库（见 requirements.txt）

### 2. 克隆代码库

```bash
git clone <repository-url>
cd MakeMoreMoney
```

### 3. 安装依赖

```bash
# 创建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate  # Windows

# 安装依赖库
pip install -r requirements.txt
```

### 4. 配置 API 凭证

在项目根目录创建一个 `.env` 文件，并设置以下环境变量：

```
LONGPORT_APP_KEY=您的应用密钥
LONGPORT_APP_SECRET=您的应用密码
LONGPORT_ACCESS_TOKEN=您的访问令牌
```

可以在 LongBridge 开发者平台获取这些凭证。

## 使用说明

### 使用启动脚本运行

我们提供了一个便捷的启动脚本来运行系统：

```bash
# 赋予脚本执行权限（如果尚未设置）
chmod +x start.sh

# 运行启动脚本（默认交易 700.HK）
./start.sh

# 指定交易标的
./start.sh --symbols "700.HK 9988.HK"

# 启动并训练模型
./start.sh --train

# 同时指定多个参数
./start.sh --symbols "AAPL.US TSLA.US" --train
```

### 手动运行

也可以直接运行 Python 脚本：

```bash
# 默认运行
python main.py

# 指定交易标的
python main.py --symbols 700.HK 9988.HK AAPL.US

# 启动前训练模型
python main.py --train
```

## 扩展系统

系统设计为高度可扩展，可以通过以下方式添加新功能：

1. **添加新的交易策略**：
   - 在 `strategy/` 目录下创建新的策略类
   - 实现预测和信号生成接口
   - 在配置文件中添加相应参数

2. **支持新的数据源**：
   - 在 `data_loader/` 目录下添加新的数据源适配器
   - 实现统一的数据访问接口
   - 在需要的地方注入新的数据源

3. **增强风险控制**：
   - 在 `execution/order_manager.py` 中添加新的风控规则
   - 在配置文件中添加相应的风控参数

## 风险控制

系统内置多重风险控制机制：

- **最大持仓限制**：控制单一股票的最大持仓量
- **每日订单限制**：限制每日最大交易次数
- **仓位比例控制**：控制单一股票占总资产的比例
- **交易间隔控制**：避免频繁交易造成的冲击成本

可以在配置文件中调整这些风险参数。

## 模块详细说明

每个模块都有自己的 README 文件，提供详细的使用说明和接口文档：

- [数据加载模块](./data_loader/README.md)
- [策略模块](./strategy/README.md)
- [订单执行模块](./execution/README.md)
- [数据库模块](./databases/README.md)

## 注意事项

- 本系统仅供学习和研究使用，实盘交易请谨慎评估风险
- 使用前请确保 LongBridge API 凭证正确且有效
- 首次使用推荐使用少量资金进行测试
- 系统运行日志保存在 `logs` 目录中，请定期检查
- 交易前请务必熟悉相关市场的交易规则和时间

## 常见问题

1. **API 连接失败**
   - 检查网络连接
   - 验证 API 凭证是否正确
   - 确认 API 地址是否可达

2. **无法生成交易信号**
   - 检查历史数据是否充足
   - 验证模型是否已训练
   - 确认实时行情是否正常接收

3. **订单执行失败**
   - 检查账户余额是否充足
   - 验证交易时间是否在市场开放时段
   - 确认订单参数是否合规

## 系统功能

- 自动获取港股/美股实时行情（Level2）
- 集成LSTM模型进行价格预测
- 根据策略信号执行委托下单
- 严格风险控制，单笔交易≤2%仓位，日亏损≥5%停止交易

## 系统架构

```
├── data_loader/   # 行情数据模块
│   ├── realtime.py     # 实时行情订阅与处理
│   └── historical.py   # 历史K线数据获取
├── strategy/      # 策略引擎
│   ├── train.py        # LSTM模型训练
│   └── signals.py      # 交易信号生成
├── execution/     # 交易执行
│   └── order_manager.py # 订单管理与执行
├── utils.py       # 工具类
├── config.yaml    # 配置文件
└── main.py        # 主程序入口
```

## 依赖环境

- Python 3.10+
- 长桥OpenAPI SDK
- TensorFlow 2.10+
- 其他依赖见requirements.txt

## 安装步骤

1. 克隆仓库

```bash
git clone https://github.com/yourusername/MakeMoreMoney.git
cd MakeMoreMoney
```

2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 配置API密钥

复制示例环境变量文件并填入你的API密钥：

```bash
cp .env.example .env
```

编辑.env文件，填入你的长桥API密钥：

```
LONG_PORT_APP_KEY=your_app_key
LONG_PORT_APP_SECRET=your_app_secret
LONG_PORT_ACCESS_TOKEN=your_access_token
```

4. 修改配置文件（根据需要）

编辑`config.yaml`文件，根据你的需求修改股票代码、风险参数等配置。

## 使用方法

### 启动交易系统

```bash
python main.py
```

### 启动交易系统并重新训练模型

```bash
python main.py --train
```

### 修改信号生成间隔

```bash
python main.py --interval 600  # 每10分钟生成一次信号
```

### 指定配置文件

```bash
python main.py --config custom_config.yaml
```

## 风险控制机制

系统内置多重风险控制机制：

1. 单笔交易不超过账户资金的2%
2. 日亏损达到5%时自动停止交易
3. 追踪止损机制，设置止损价格保护盈利
4. 异步订单队列和信号量控制，防止过度交易

## 数据缓存机制

历史K线数据会缓存到本地`data_cache`目录，可以减少API调用并提升性能。

## 模型训练

LSTM模型使用多股票的历史K线数据进行训练，能够适应不同市场环境。模型训练历史和结果保存在`models/history`目录。

## 日志记录

系统日志默认保存在`logs/trading.log`文件中，包含详细的交易信号、订单执行和异常信息。

## 开发者说明

### 添加新策略

1. 在`strategy`目录下创建新的策略模块
2. 实现`SignalGenerator`接口或继承现有类
3. 在`main.py`中注册新策略

### 扩展风险控制

可以在`execution/order_manager.py`中的`_check_risk_control`方法中添加新的风险控制规则。

## 许可证

[MIT](LICENSE)

## 致谢

- [长桥证券OpenAPI](https://open.longportapp.com/) - 提供行情和交易接口
- [TensorFlow](https://www.tensorflow.org/) - 深度学习框架 