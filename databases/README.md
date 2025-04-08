# 数据库模块 (Databases)

本模块负责管理系统中的持久化数据存储，包括历史行情数据、交易记录、订单历史和系统日志等。设计目标是提供统一的数据访问层，隔离数据存储细节，保持与其他模块的解耦合。

## 模块组成

本模块由以下主要组件组成：

1. **数据库初始化与管理** (db.py)：负责数据库连接和初始化
2. **数据模型** (models.py)：定义数据表结构和关系
3. **数据访问仓库** (repository.py)：提供数据访问接口
4. **数据库迁移** (migrations/)：管理数据库架构变更

## 接口设计

为保持模块间的解耦合，数据库模块通过以下方式向其他模块提供服务：

### 数据库初始化

```python
# 初始化数据库
from databases.db import init_db
init_db()
```

### 数据仓库

```python
# 获取交易记录仓库
from databases.repository import TradeRepository
trade_repo = TradeRepository()

# 保存交易记录
trade_record = {
    "symbol": "700.HK",
    "order_id": "O-123456",
    "side": "Buy",
    "quantity": 100,
    "price": 440.2,
    "timestamp": datetime.now()
}
trade_repo.save(trade_record)

# 查询交易记录
trades = trade_repo.find_by_symbol("700.HK")
today_trades = trade_repo.find_by_date(date.today())
```

### 数据模型

```python
# 直接使用数据模型
from databases.models import Trade, Order, Position

# 创建新交易记录
new_trade = Trade(
    symbol="700.HK",
    order_id="O-123456",
    side="Buy",
    quantity=100,
    price=440.2,
    timestamp=datetime.now()
)

# 保存到数据库
session.add(new_trade)
session.commit()
```

## 解耦合设计

本模块遵循以下解耦合原则：

1. **仓库模式**：通过仓库类封装数据访问逻辑，提供面向业务的接口
2. **ORM映射**：使用ORM框架映射数据模型和数据表，隐藏SQL细节
3. **接口一致性**：提供统一的数据访问接口，不暴露底层实现
4. **迁移管理**：使用迁移脚本管理数据库架构变更，确保兼容性
5. **连接池管理**：内部管理数据库连接池，优化性能和资源使用

## 数据模型设计

本模块包含以下主要数据模型：

1. **Trade**: 交易记录
   - symbol: 股票代码
   - order_id: 订单ID
   - side: 交易方向 (Buy/Sell)
   - quantity: 数量
   - price: 价格
   - timestamp: 交易时间
   - status: 交易状态

2. **Order**: 订单记录
   - order_id: 订单ID
   - symbol: 股票代码
   - side: 交易方向
   - order_type: 订单类型
   - quantity: 数量
   - price: 价格
   - status: 订单状态
   - created_at: 创建时间
   - updated_at: 更新时间

3. **Signal**: 信号记录
   - symbol: 股票代码
   - signal_type: 信号类型
   - price: 信号价格
   - confidence: 置信度
   - generated_at: 生成时间
   - executed: 是否已执行

4. **DailyPrice**: 每日价格数据
   - symbol: 股票代码
   - date: 日期
   - open: 开盘价
   - high: 最高价
   - low: 最低价
   - close: 收盘价
   - volume: 成交量

## 与其他模块交互

数据库模块与其他模块的交互通过以下机制：

1. **数据持久化**：其他模块调用数据库模块保存数据
2. **数据查询**：其他模块从数据库模块查询历史数据
3. **事务管理**：数据库模块提供事务支持，确保数据一致性

示例：
```python
# 订单执行模块保存订单记录
from databases.repository import OrderRepository
order_repo = OrderRepository()

def on_order_executed(order_result):
    # 保存订单到数据库
    order_repo.save({
        "order_id": order_result.order_id,
        "symbol": order_result.symbol,
        "side": order_result.side.value,
        "quantity": order_result.quantity,
        "price": order_result.price,
        "status": order_result.status.value,
        "created_at": order_result.submitted_at,
        "updated_at": datetime.now()
    })
```

## 配置项

数据库模块使用以下配置项：

```yaml
database:
  url: "sqlite:///databases/trading_system.db"  # 数据库连接字符串
  echo: false  # 是否输出SQL语句
  pool_size: 5  # 连接池大小
  pool_recycle: 3600  # 连接回收时间（秒）
  pool_timeout: 30  # 连接获取超时时间（秒）
```

## 依赖

- SQLAlchemy
- Alembic (数据库迁移)

## 注意事项

1. 首次运行系统时会自动创建数据库和表结构
2. 数据库文件默认保存在 `databases/trading_system.db` 中
3. 定期备份数据库文件以防数据丢失
4. 大量数据写入时可能影响性能，请考虑批量操作
5. 长时间运行可能需要定期进行数据清理或归档 