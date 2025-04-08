# 订单执行模块 (Execution)

本模块负责处理交易指令、执行订单和管理订单状态。设计目标是提供安全、可靠的交易执行机制，实现交易策略与实际下单的桥梁，同时保持与其他模块的解耦合。

## 模块组成

本模块由以下主要组件组成：

1. **OrderManager** (order_manager.py)：负责订单管理和执行
2. **OrderResult** (order_manager.py)：封装订单结果信息

## 接口设计

为保持模块间的解耦合，订单执行模块通过以下方式向其他模块提供服务：

### OrderManager

```python
# 初始化
order_mgr = OrderManager(config_loader)

# 初始化交易上下文
await order_mgr.initialize()

# 注册订单回调
order_mgr.register_order_callback(callback_function)

# 处理交易信号
result = await order_mgr.process_signal(signal)

# 提交订单
order_result = await order_mgr.submit_order(symbol, side, order_type, quantity, price)

# 取消订单
success = await order_mgr.cancel_order(order_id)

# 获取订单状态
order = await order_mgr.get_order_status(order_id)

# 获取今日订单
orders = await order_mgr.get_today_orders()

# 获取账户余额
balance = await order_mgr.get_account_balance()

# 获取持仓
positions = await order_mgr.get_positions()

# 关闭
await order_mgr.close()
```

### OrderResult

```python
# 获取订单状态信息
order_result.is_filled()  # 是否已成交
order_result.is_active()  # 是否活跃
order_result.is_canceled()  # 是否已取消
order_result.is_rejected()  # 是否被拒绝

# 获取订单详情
order_id = order_result.order_id
symbol = order_result.symbol
executed_quantity = order_result.executed_quantity
executed_price = order_result.executed_price
```

## 解耦合设计

本模块遵循以下解耦合原则：

1. **依赖注入**：通过构造函数接收配置对象，而非直接读取配置
2. **回调机制**：通过回调函数向外部模块通知订单状态变化
3. **接口一致性**：提供统一的交易接口，隐藏底层交易API的复杂性
4. **风控内置**：内置风险控制逻辑，确保交易安全
5. **状态封装**：通过OrderResult封装订单状态，提供一致的访问接口

## 与其他模块交互

订单执行模块与其他模块的交互通过以下机制：

1. **信号接收**：从策略模块接收交易信号（通过process_signal方法）
2. **状态通知**：通过回调函数通知其他模块订单状态变化
3. **配置读取**：从配置加载器读取风控参数和交易规则

示例：
```python
# 主程序注册订单回调
def on_order_update(order_result):
    # 处理订单状态更新
    print(f"订单状态更新: {order_result}")

# 注册回调
order_mgr.register_order_callback(on_order_update)

# 处理信号
async def on_signal(signal):
    await order_mgr.process_signal(signal)

# 策略模块注册信号回调
signal_generator.register_signal_callback(on_signal)
```

## 风险控制

订单执行模块内置多重风险控制机制：

```yaml
execution:
  risk_control:
    max_order_size: 1000  # 单笔最大交易数量
    max_daily_orders: 50  # 每日最大订单数
    position_pct: 5.0  # 单只股票最大仓位比例（占账户总资产）
    max_drawdown_pct: 5.0  # 单日最大回撤限制（百分比）
    daily_loss_limit_pct: 2.0  # 单日最大亏损限制（占总资产百分比）
```

风控检查流程：
1. 检查订单数量是否超过限制
2. 检查今日订单数是否超过限制
3. 检查持仓比例是否超过限制
4. 检查亏损是否超过限制

只有通过所有风控检查，订单才会被执行。

## 订单类型

支持以下订单类型：
- 限价单 (LO)
- 增强限价单 (ELO)
- 市价单 (MO)

## 依赖

- LongPort OpenAPI SDK
- asyncio
- decimal

## 注意事项

1. 使用本模块前需确保已正确设置 LongPort API 凭证
2. 执行交易前请了解相应市场的交易规则和时间
3. 首次使用建议使用小额资金测试交易执行的可靠性
4. 实盘交易前务必测试风控参数的合理性
5. 系统关闭前需调用 `close()` 方法释放资源 