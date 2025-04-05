# LongPort SDK 增强封装

这个模块是对长桥(LongPort/LongBridge)官方SDK的增强封装，添加了以下功能：

- **自动重试机制**：处理网络波动和临时错误
- **连接管理**：自动处理连接的建立、维护和重连
- **WebSocket支持**：方便地订阅和处理实时市场数据
- **错误处理**：更好的日志记录和错误处理
- **SSL/TLS处理**：智能处理SSL握手问题
- **统一API**：简化API调用，使用统一的调用风格

## 环境要求

- Python 3.7+
- 长桥API账户及凭证
- 正确的网络配置（可能需要修改hosts文件）

## 安装

### 1. 安装依赖

```bash
pip install longport python-dotenv
```

### 2. 配置环境变量

创建`.env`文件，配置以下变量：

```
# LongPort API Credentials
LONG_PORT_APP_KEY=your_app_key
LONG_PORT_APP_SECRET=your_app_secret
LONG_PORT_ACCESS_TOKEN=your_access_token

# API URLs
API_BASE_URL=https://open-api.longportapp.com
API_WS_URL=wss://open-api-quote.longportapp.com/v2

# SSL设置（如果有SSL问题，可以临时禁用SSL验证，但不推荐用于生产环境）
# LONGPORT_DISABLE_SSL_VERIFY=true

# 如果需要，可以配置代理
# HTTP_PROXY=http://127.0.0.1:7890
# HTTPS_PROXY=http://127.0.0.1:7890
```

### 3. 网络问题处理

如果遇到DNS污染问题，可以通过修改hosts文件来解决：

```
# Windows: C:\Windows\System32\drivers\etc\hosts
# Linux/Mac: /etc/hosts

31.13.95.169 open-api.longportapp.com
31.13.95.18 api-gateway.longportapp.com
```

修改hosts文件后，你可能需要刷新DNS缓存。在macOS上，可以运行：

```bash
sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
```

## 使用方法

### 基本使用

```python
from longbridge_quant.api_client.client import LongPortClient
from longport.openapi import Market, Period, AdjustType

# 创建客户端
client = LongPortClient()

# 获取账户余额
balance = client.get_account_balance()
print(f"账户余额: {balance}")

# 获取股票实时报价
quotes = client.get_quote(["700.HK", "AAPL.US"])
print(f"实时报价: {quotes}")

# 获取K线数据
candles = client.get_candlesticks(
    symbol="700.HK",
    period=Period.Day,
    count=10,
    adjust_type=AdjustType.NoAdjust
)
print(f"K线数据: {candles}")

# 不要忘记关闭连接
client.close()
```

### 使用WebSocket订阅实时数据

```python
import asyncio
from longbridge_quant.api_client.client import LongPortClient
from longport.openapi import SubType

def quote_callback(symbol, event):
    print(f"收到 {symbol} 的报价更新: 最新价格 {event.last_done}")

async def subscribe_real_time_data():
    # 创建启用WebSocket的客户端
    client = LongPortClient(use_websocket=True)
    
    # 注册回调函数
    client.register_quote_callback("700.HK", quote_callback)
    
    # 订阅行情数据
    client.subscribe_quotes(["700.HK"], [SubType.Quote])
    
    # 保持程序运行，持续接收数据
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        # 取消订阅和关闭连接
        client.unsubscribe_quotes(["700.HK"], [SubType.Quote])
        client.close()

# 运行异步程序
asyncio.run(subscribe_real_time_data())
```

### 交易操作

```python
from decimal import Decimal
from longbridge_quant.api_client.client import LongPortClient
from longport.openapi import OrderSide, OrderType, TimeInForceType

# 创建客户端
client = LongPortClient()

# 下单
result = client.create_order(
    symbol="700.HK",
    order_type=OrderType.LO,  # 限价单
    side=OrderSide.Buy,
    quantity=100,  # 100股
    time_in_force=TimeInForceType.Day,
    submitted_price=Decimal("500.00")  # 限价500港币
)
print(f"订单ID: {result.order_id}")

# 查询今日订单
orders = client.get_today_orders()
print(f"今日订单: {orders}")

# 撤单
cancel_result = client.cancel_order(result.order_id)
print(f"撤单结果: {cancel_result}")

# 获取持仓
positions = client.get_positions()
print(f"当前持仓: {positions}")

# 不要忘记关闭连接
client.close()
```

## 高级用法

查看 `examples` 目录中的更多示例：

- `trade_example.py`: 综合演示API的使用，包括行情获取、交易操作和WebSocket订阅
- 更多示例正在开发中...

## 常见问题

1. **SSL/TLS握手失败**：
   - 检查网络连接和hosts文件配置
   - 临时可以设置 `LONGPORT_DISABLE_SSL_VERIFY=true`，但不建议用于生产环境

2. **连接超时**：
   - 可能是网络问题，API连接类会自动重试
   - 检查防火墙设置，确保允许WebSocket连接

3. **API凭证错误**：
   - 确保 `.env` 文件中的API凭证正确
   - 检查API凭证是否过期，如果过期则需要重新生成

## 贡献

欢迎提出改进建议和PR。

## 许可证

MIT 