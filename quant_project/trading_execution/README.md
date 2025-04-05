# 量化交易系统 - 交易执行层

交易执行层是量化交易系统的核心组件，负责接收交易信号，执行订单，管理仓位，并提供风险控制。本模块支持多种交易接口，包括模拟交易和真实交易（如长桥证券）。

## 功能特点

- **多接口支持**：支持模拟交易和长桥证券接口，可轻松扩展支持其他交易接口
- **风险管理**：内置风险控制机制，包括单笔交易限额、持仓限额、日内交易限额等
- **策略管理**：支持注册、启动、停止策略
- **信号处理**：接收和处理交易信号，转化为具体订单
- **订单管理**：创建、提交、撤销订单，查询订单状态
- **持仓管理**：追踪持仓变化，计算盈亏
- **REST API**：提供完整的REST API，支持远程控制和监控
- **高可用性**：心跳检测、错误处理、日志记录等确保系统稳定性

## 系统架构

```
+-------------------+   +-------------------+   +-------------------+
|  策略研究层        |   |  量化信号生成器    |   |  外部系统/手动交易 |
+--------+----------+   +--------+----------+   +--------+----------+
         |                      |                       |
         v                      v                       v
+------------------------------------------------------------------+
|                           REST API                               |
+------------------------------------------------------------------+
                              |
                              v
+------------------------------------------------------------------+
|                         交易服务器                                |
|                                                                  |
|  +---------------+   +----------------+   +------------------+   |
|  |  策略管理      |   |  信号处理       |   |  风险管理        |   |
|  +---------------+   +----------------+   +------------------+   |
|                              |                                   |
|                              v                                   |
|  +---------------+   +----------------+   +------------------+   |
|  |  订单管理      |   |  仓位管理       |   |  行情数据        |   |
|  +---------------+   +----------------+   +------------------+   |
|                              |                                   |
+------------------------------|-----------------------------------+
                              |
                              v
+------------------------------------------------------------------+
|                         交易接口抽象层                            |
+------------------------------------------------------------------+
         |                      |                       |
         v                      v                       v
+-------------------+   +-------------------+   +-------------------+
|  模拟交易接口      |   |  长桥证券接口      |   |  其他交易接口     |
+-------------------+   +-------------------+   +-------------------+
```

## 安装与配置

### 环境要求

- Python 3.9+
- 依赖库：见 `trading_execution_env.yml`

### 安装步骤

1. 创建conda环境

```bash
conda env create -f ../configs/trading_execution_env.yml
```

2. 激活环境

```bash
conda activate trading_execution_env
```

3. 配置环境变量

在项目根目录下创建 `.env` 文件，配置必要的环境变量：

```bash
# 交易接口选择: "mock" 或 "longport"
TRADING_INTERFACE=mock

# 长桥证券API配置（如果使用长桥接口）
LONGPORT_APP_KEY=your_app_key
LONGPORT_APP_SECRET=your_app_secret
LONGPORT_ACCESS_TOKEN=your_access_token

# API服务端口
TRADING_API_PORT=8002

# 风险控制参数
MAX_POSITION_VALUE=500000
MAX_DAILY_ORDERS=100
MAX_DAILY_TURNOVER=1000000
MAX_ORDER_VALUE=100000
MAX_DRAWDOWN=0.1
```

## 使用方法

### 启动服务

```bash
cd /path/to/project
conda activate trading_execution_env
python trading_execution/trading_server.py
```

或使用脚本启动：

```bash
./scripts/env_manager.sh start-trading
```

### 通过API控制

服务启动后，可以通过REST API进行控制：

1. 获取账户信息

```bash
curl http://localhost:8002/api/account
```

2. 下单

```bash
curl -X POST http://localhost:8002/api/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol": "700.HK", "direction": "buy", "quantity": 100, "price": 500.0}'
```

3. 获取订单信息

```bash
curl http://localhost:8002/api/orders
```

4. 注册策略

```bash
curl -X POST http://localhost:8002/api/strategies \
  -H "Content-Type: application/json" \
  -d '{"strategy_id": "my_strategy", "name": "我的策略", "description": "示例策略", "symbols": ["AAPL.US", "MSFT.US"]}'
```

## API文档

服务启动后，可通过以下地址查看完整的API文档：

```
http://localhost:8002/docs
```

### 主要API端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/status` | GET | 获取服务器状态 |
| `/api/heartbeat` | GET | 心跳检测 |
| `/api/account` | GET | 获取账户信息 |
| `/api/positions` | GET | 获取持仓信息 |
| `/api/orders` | GET | 获取订单信息 |
| `/api/orders` | POST | 下单 |
| `/api/orders/{order_id}` | DELETE | 撤单 |
| `/api/signals` | POST | 处理交易信号 |
| `/api/strategies` | GET | 获取所有策略信息 |
| `/api/strategies` | POST | 注册策略 |
| `/api/strategies/{strategy_id}` | GET | 获取特定策略信息 |
| `/api/strategies/{strategy_id}/start` | POST | 启动策略 |
| `/api/strategies/{strategy_id}/stop` | POST | 停止策略 |
| `/api/trading/enable` | POST | 启用交易 |
| `/api/trading/disable` | POST | 禁用交易 |
| `/api/risk/limits` | PUT | 更新风险限制 |
| `/api/market-data` | GET | 获取市场数据 |

## 交易接口

### 模拟交易接口 (MockTradingInterface)

模拟交易接口提供完整的交易模拟，包括订单执行、持仓管理、资金计算等功能。适用于策略测试和开发阶段。

特点：
- 支持多种订单类型：市价单、限价单
- 模拟订单撮合机制
- 计算持仓盈亏
- 生成模拟市场数据

### 长桥证券接口 (LongPortInterface)

长桥证券接口连接真实的长桥证券交易API，支持港股、美股、A股等市场的真实交易。

要求：
- 安装长桥SDK：`pip install longport`
- 配置有效的API密钥
- 长桥证券账户资金

## 扩展与二次开发

### 添加新的交易接口

1. 创建新的接口类，继承 `TradingInterface` 抽象类
2. 实现所有必要的方法：账户信息、持仓查询、订单管理等
3. 在 `TradingServer` 初始化方法中添加新接口的支持

示例：

```python
class NewBrokerInterface(TradingInterface):
    """新的券商接口实现"""
    
    def __init__(self):
        super().__init__()
        # 初始化接口
        
    def get_account(self, account_id: str = None) -> Dict[str, Any]:
        # 实现接口逻辑
        ...
```

### 修改风险控制逻辑

风险控制逻辑位于 `TradingServer` 类的 `_check_risk_limits` 方法中，可以根据需要进行修改或扩展。

## 常见问题

**Q: 如何在开发过程中避免真实交易?**
A: 将环境变量 `TRADING_INTERFACE` 设置为 "mock"，使用模拟交易接口进行测试。

**Q: 如何处理网络连接问题?**
A: 系统内置了重试机制和错误处理，遇到网络问题会记录日志并尝试恢复。对于长时间的连接中断，建议配置监控告警。

**Q: 系统支持哪些订单类型?**
A: 目前支持市价单和限价单，可以通过扩展交易接口添加更多订单类型的支持。

## 日志与监控

日志文件保存在 `shared_data/logs` 目录下，按日期命名。

建议通过API定期调用 `/api/heartbeat` 和 `/api/status` 端点监控服务健康状态。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。

## 联系与支持

如有问题或建议，请联系项目维护者。

# 量化交易执行层服务

本服务提供统一的交易执行接口，支持连接到真实交易账户（LongPort长桥证券）。

## 功能特点

- 提供统一的交易API，支持下单、撤单、查询账户等操作
- 支持直接接入长桥证券API进行实盘交易
- 支持风控规则设置，防止意外风险
- 提供RESTful API接口，方便各类策略服务调用
- 支持交易信号处理和策略执行

## 环境配置

### 安装依赖

```bash
pip install longbridge pandas numpy fastapi uvicorn python-dotenv
```

### 配置环境变量

参考`longport_env_example.txt`文件，创建`.env`文件并设置相关参数：

```bash
# 复制示例环境变量文件
cp longport_env_example.txt .env

# 编辑.env文件填入您的API凭证
nano .env
```

重要参数说明：
- `LONG_PORT_APP_KEY`: 长桥API密钥
- `LONG_PORT_APP_SECRET`: 长桥API密钥
- `LONG_PORT_ACCESS_TOKEN`: 长桥访问令牌
- `LONGPORT_DISABLE_SSL_VERIFY`: 是否禁用SSL验证（仅开发环境使用）

## 使用方法

### 启动交易服务器

```bash
python trading_server.py
```

启动选项：
- `--port`: 指定API服务器端口（默认8000）
- `--debug`: 开启调试模式
- `--interface`: 指定交易接口（默认longport）
- `--create-env`: 创建环境变量示例文件

### API接口

启动服务器后，可通过以下REST接口进行操作：

- `GET /api/account`: 获取账户信息
- `GET /api/positions`: 获取持仓信息
- `GET /api/orders`: 获取订单信息
- `POST /api/orders`: 下单
- `DELETE /api/orders/{order_id}`: 撤单

### 代码中使用

```python
from quant_project.trading_execution.trading_server import LongPortInterface

# 创建长桥交易接口
longport = LongPortInterface()

# 获取账户信息
account_info = longport.get_account_info()
print(f"账户资金: {account_info['total_cash']}")

# 获取持仓
positions = longport.get_positions()
for pos in positions:
    print(f"持有 {pos['symbol']} {pos['quantity']} 股")

# 下单（示例）
order_request = {
    "symbol": "700.HK",
    "direction": "buy",
    "quantity": 100,
    "price": 500.0,
    "order_type": "limit"
}
result = longport.place_order(order_request)
print(f"下单结果: {result}")
```

## 异常处理

LongPort接口可能出现的常见错误：

1. 连接错误: 通常由网络问题引起，需检查网络连接
2. 认证错误: 检查API凭证是否正确设置
3. 订单错误: 查看订单参数是否合规

## 风险控制

交易服务器内置风控规则，可通过环境变量配置：

- `RISK_MAX_POSITION_VALUE`: 最大持仓市值
- `RISK_MAX_DAILY_ORDERS`: 每日最大订单数
- `RISK_MAX_DAILY_TURNOVER`: 每日最大成交金额

## 高级用法

### 定制化交易接口

可以继承`TradingInterface`基类实现自定义交易接口：

```python
class CustomTradingInterface(TradingInterface):
    def __init__(self):
        super().__init__()
        # 您的初始化代码
        
    def get_account_info(self, account_id=None):
        # 实现获取账户信息逻辑
        pass
        
    # 实现其他必要方法...
```

### 事件处理

LongPort接口支持注册回调函数处理市场数据更新：

```python
# 订阅实时行情
longport.quote_ctx.subscribe(symbols=["700.HK"], sub_types=[SubType.Quote])

# 注册回调
def on_quote_update(symbol, data):
    print(f"收到 {symbol} 行情更新: {data.last_done}")

longport.register_quote_callback("700.HK", on_quote_update)
``` 