# 长桥API增强SDK项目

## 项目概述

这个项目是对长桥证券API的增强封装，解决了SSL/TLS连接问题，并提供了更好的可靠性和易用性。主要特点包括：

- **自动重试机制**: 自动处理网络波动和临时错误
- **WebSocket封装**: 方便地处理实时市场数据
- **连接管理**: 智能处理连接和重连
- **统一API**: 简化API调用方式
- **优化了SSL处理**: 解决了DNS污染和SSL握手问题

## 项目结构

```
MakeMoreMoney/
│
├── longbridge_quant/           # 主要功能模块
│   ├── __init__.py             # 模块初始化
│   ├── README.md               # 模块使用说明
│   │
│   └── api_client/             # API客户端
│       ├── __init__.py         # 子模块初始化
│       └── client.py           # 增强型API客户端
│
├── examples/                   # 使用示例
│   └── trade_example.py        # 交易和数据订阅示例
│
├── .env                        # 环境配置文件
└── README.md                   # 本文件
```

## 快速开始

### 1. 安装依赖

```bash
pip install longport python-dotenv
```

### 2. 配置API凭证

创建或编辑`.env`文件：

```ini
# LongPort API Credentials
LONG_PORT_APP_KEY=your_app_key
LONG_PORT_APP_SECRET=your_app_secret
LONG_PORT_ACCESS_TOKEN=your_access_token

# API URLs
API_BASE_URL=https://open-api.longportapp.com
API_WS_URL=wss://open-api-quote.longportapp.com/v2
```

### 3. 运行示例

```bash
python examples/trade_example.py
```

## 网络配置

如果您遇到DNS污染或SSL握手问题，请修改hosts文件：

```
# Windows: C:\Windows\System32\drivers\etc\hosts
# Linux/Mac: /etc/hosts

31.13.95.169 open-api.longportapp.com
31.13.95.18 api-gateway.longportapp.com
```

然后刷新DNS缓存：

```bash
# macOS
sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder

# Windows
ipconfig /flushdns
```

## 更多信息

详细的使用说明，请查看 [longbridge_quant/README.md](longbridge_quant/README.md)。

## 许可证

MIT
