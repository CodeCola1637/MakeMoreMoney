# LongBridge Quant 项目

一个基于LongPort API的量化交易系统，支持策略研究和实盘交易。

## 系统架构

该项目由以下主要模块组成：

1. **交易执行层**：连接交易接口，负责订单执行和市场数据获取
2. **策略研究层**：实现交易策略，包括信号生成和回测
3. **API服务层**：提供统一的REST API接口，连接策略与交易执行

### 目录结构

```
quant_project/
├── common/               # 共享工具和函数
│   ├── __init__.py      
│   └── shared_paths.py   # 文件路径和数据存储工具
│
├── trading_execution/    # 交易执行层
│   ├── __init__.py
│   ├── trading_server.py # 交易服务器实现
│   └── api_server.py     # API服务器实现
│
├── strategy_research/    # 策略研究层
│   ├── __init__.py
│   ├── strategies/       # 策略实现
│   │   ├── __init__.py
│   │   ├── template.py   # 策略模板基类
│   │   └── dual_ma.py    # 双均线策略实现
│   └── run_dual_ma.py    # 运行双均线策略脚本
│
├── run_system.sh         # 系统启动脚本
└── README.md             # 本文档
```

## 核心组件说明

### 交易执行层

- **TradingServer**：交易服务器核心，管理交易接口和订单执行
- **MockTradingInterface**：模拟交易接口，用于测试和模拟交易
- **LongPortInterface**：连接龙桥证券API的实盘交易接口

### 策略模块

- **StrategyTemplate**：所有策略的基类，提供通用的策略框架
- **DualMAStrategy**：双均线交叉策略实现

### API服务

- **API服务器**：FastAPI实现的REST API，提供订单、行情、账户信息等接口

## 快速开始

### 安装依赖

```bash
# 创建并激活虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 安装依赖
pip install fastapi uvicorn pandas numpy requests longport
```

### 启动系统

```bash
# 启动系统（使用模拟接口）
./run_system.sh mock

# 启动系统（使用龙桥接口）
./run_system.sh longport
```

### 运行策略

```bash
# 运行双均线策略（腾讯示例）
python -m quant_project.strategy_research.run_dual_ma --symbol 700.HK --fast 5 --slow 20
```

## API接口说明

系统提供以下REST API接口：

- **GET /api/status**：获取系统状态
- **GET /api/quotes?symbols=700.HK,9988.HK**：获取行情数据
- **GET /api/history?symbol=700.HK&period=day&count=30**：获取历史数据
- **GET /api/account**：获取账户信息
- **GET /api/positions**：获取持仓信息
- **GET /api/orders**：获取订单信息
- **POST /api/orders**：创建订单
- **DELETE /api/orders/{order_id}**：取消订单
- **GET /api/strategies**：获取策略列表

API服务器默认运行在 `http://localhost:8002`

## 自定义策略开发

要创建新的交易策略，只需继承`StrategyTemplate`类并实现必要的方法：

```python
from quant_project.strategy_research.strategies.template import StrategyTemplate, TickData

class MyCustomStrategy(StrategyTemplate):
    # 定义策略参数
    parameters = {
        "param1": 10,
        "param2": 20
    }
    
    def on_tick(self, tick: TickData):
        # 实现您的交易逻辑
        pass
        
    def on_start(self):
        # 策略启动时的初始化
        pass
```

## 配置LongPort接口

使用LongPort接口需要正确配置环境变量：

1. 创建`.env`文件在项目根目录
2. 添加以下配置：

```
LONGPORT_APP_KEY=您的AppKey
LONGPORT_APP_SECRET=您的AppSecret
LONGPORT_ACCESS_TOKEN=您的AccessToken
```

## 注意事项

- 模拟接口仅用于测试和开发，不反映真实市场情况
- 实盘交易前请确保您已了解交易规则和风险 