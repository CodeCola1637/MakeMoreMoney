# 数据加载模块 (Data Loader)

本模块负责从各种数据源加载和处理数据，包括实时行情数据和历史数据。设计目标是提供清晰的数据访问接口，使其他模块可以方便地获取所需数据，同时保持与其他模块的解耦合。

## 模块组成

本模块由以下主要组件组成：

1. **RealtimeDataManager** (realtime.py)：负责订阅和处理实时市场数据
2. **HistoricalDataLoader** (historical.py)：负责加载和处理历史行情数据

## 接口设计

为保持模块间的解耦合，数据加载模块通过以下方式向其他模块提供服务：

### RealtimeDataManager

```python
# 初始化
realtime_mgr = RealtimeDataManager(config_loader)

# 初始化连接
await realtime_mgr.initialize()

# 订阅行情
await realtime_mgr.subscribe([symbol], [SubType.Quote])

# 获取最新报价
quote = realtime_mgr.get_latest_quote(symbol)

# 批量获取实时报价
quotes = await realtime_mgr.get_quote([symbol1, symbol2])

# 获取K线数据
candles = await realtime_mgr.get_candlesticks(symbol, period="1d", count=100)

# 关闭连接
await realtime_mgr.close()
```

### HistoricalDataLoader

```python
# 初始化
hist_loader = HistoricalDataLoader(config_loader)

# 获取历史K线数据
candles = await hist_loader.get_candlesticks(symbol, period="1d", start_time=None, end_time=None)

# 加载CSV数据
data = hist_loader.load_from_csv(file_path)

# 保存数据到CSV
hist_loader.save_to_csv(data, file_path)
```

## 解耦合设计

本模块遵循以下解耦合原则：

1. **依赖注入**：通过构造函数接收配置对象，而非直接读取配置文件
2. **事件驱动**：通过回调机制处理实时数据更新，其他模块可注册回调而无需直接依赖
3. **接口一致性**：提供统一的数据访问接口，隐藏底层实现细节
4. **异常封装**：在模块内部处理API调用异常，不向外部暴露底层错误

## 与其他模块交互

数据加载模块与其他模块的交互通过以下机制：

1. **回调注册**：其他模块（如信号生成器）可以注册回调函数，在数据更新时被通知
2. **数据访问接口**：其他模块可以调用本模块提供的方法获取所需数据
3. **事件通知**：数据更新时，通过回调函数通知已注册的模块

示例：
```python
# 策略模块注册回调
def on_quote_update(symbol, quote):
    # 处理最新行情数据
    pass

# 向数据管理器注册回调
realtime_mgr.register_quote_callback("700.HK", on_quote_update)
```

## 配置项

数据加载模块使用以下配置项：

```yaml
data_loader:
  historical:
    default_period: "1d"  # 默认K线周期
    cache_dir: "data_cache"  # 缓存目录
    max_cache_days: 30  # 最大缓存天数
  
  realtime:
    reconnect_interval: 10  # 重连间隔（秒）
    max_reconnect_attempts: 3  # 最大重连次数
    request_timeout: 30  # 请求超时（秒）
```

## 依赖

- LongPort OpenAPI SDK
- pandas
- numpy

## 注意事项

1. 使用本模块前需确保已正确设置 LongPort API 凭证
2. 实时数据订阅需要网络连接和对应市场的访问权限
3. 历史数据加载可能受API请求限制，建议合理使用缓存机制
4. 在系统关闭时，需正确调用 `close()` 方法释放资源 