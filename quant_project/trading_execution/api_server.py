"""
交易服务器API接口

为策略提供统一的API接口，代理底层交易接口
"""
import os
import sys
import json
import logging
import uvicorn
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query, Path, Body
from pydantic import BaseModel, Field
from datetime import datetime

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# 导入交易服务器
from quant_project.trading_execution.simple_server import (
    SimpleServer, MockTradingInterface
)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_server")

# 创建FastAPI应用
app = FastAPI(
    title="量化交易API服务",
    description="为量化交易策略提供统一的交易和行情API接口",
    version="0.1.0"
)

# 数据模型
class OrderRequest(BaseModel):
    """订单请求模型"""
    symbol: str = Field(..., description="交易品种代码")
    direction: str = Field(..., description="交易方向: buy 或 sell")
    quantity: int = Field(..., gt=0, description="交易数量")
    price: Optional[float] = Field(None, description="价格，市价单可为空")
    order_type: str = Field("limit", description="订单类型: limit 或 market")
    strategy_id: Optional[str] = Field(None, description="策略ID")
    
class HistoryRequest(BaseModel):
    """历史数据请求模型"""
    symbol: str = Field(..., description="交易品种代码")
    period: str = Field("day", description="数据周期: tick, 1min, 5min, day, week, month")
    start_time: Optional[str] = Field(None, description="开始时间 (ISO格式)")
    end_time: Optional[str] = Field(None, description="结束时间 (ISO格式)")
    count: Optional[int] = Field(None, description="获取条数")

# 全局变量
trading_server = None
interface_type = "mock"  # 默认使用模拟接口

@app.on_event("startup")
async def startup_event():
    """服务启动时初始化交易服务器"""
    global trading_server, interface_type
    
    # 尝试读取配置
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
            interface_type = config.get("interface_type", "mock")
    except:
        logger.warning("未找到配置文件，使用默认配置")
    
    # 创建交易服务器
    trading_interface = MockTradingInterface()
    trading_server = SimpleServer(trading_interface)
    
    logger.info(f"交易服务器初始化完成, 使用{interface_type}接口")

@app.get("/api/status")
async def get_status():
    """获取服务状态"""
    if trading_server is None:
        return {"status": "initializing", "interface": None}
    
    return {
        "status": "running",
        "interface": interface_type,
        "server_time": datetime.now().isoformat()
    }

@app.get("/api/quotes")
async def get_quotes(symbols: str = Query(..., description="交易品种代码，多个用逗号分隔")):
    """获取行情数据"""
    if trading_server is None:
        raise HTTPException(status_code=503, detail="服务正在初始化中")
    
    symbol_list = [s.strip() for s in symbols.split(",")]
    
    try:
        quotes = trading_server.get_market_data(symbol_list)
        return quotes
    except Exception as e:
        logger.error(f"获取行情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取行情失败: {str(e)}")

@app.get("/api/history")
async def get_history_data(
    symbol: str = Query(..., description="交易品种代码"),
    period: str = Query("day", description="数据周期"),
    count: int = Query(None, description="获取条数"),
    start_time: str = Query(None, description="开始时间"),
    end_time: str = Query(None, description="结束时间")
):
    """获取历史数据"""
    if trading_server is None:
        raise HTTPException(status_code=503, detail="服务正在初始化中")
    
    try:
        history_data = trading_server.get_history_data(
            symbol=symbol,
            period=period,
            count=count,
            start_time=start_time,
            end_time=end_time
        )
        return history_data
    except Exception as e:
        logger.error(f"获取历史数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取历史数据失败: {str(e)}")

@app.get("/api/account")
async def get_account_info():
    """获取账户信息"""
    if trading_server is None:
        raise HTTPException(status_code=503, detail="服务正在初始化中")
    
    try:
        account_info = trading_server.get_account_info()
        return account_info
    except Exception as e:
        logger.error(f"获取账户信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取账户信息失败: {str(e)}")

@app.get("/api/positions")
async def get_positions(symbol: str = Query(None, description="交易品种代码，不指定则获取所有持仓")):
    """获取持仓信息"""
    if trading_server is None:
        raise HTTPException(status_code=503, detail="服务正在初始化中")
    
    try:
        positions = trading_server.get_positions(symbol)
        return positions
    except Exception as e:
        logger.error(f"获取持仓信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取持仓信息失败: {str(e)}")

@app.get("/api/orders")
async def get_orders(
    symbol: str = Query(None, description="交易品种代码"),
    status: str = Query(None, description="订单状态"),
    start_time: str = Query(None, description="开始时间"),
    end_time: str = Query(None, description="结束时间")
):
    """获取订单信息"""
    if trading_server is None:
        raise HTTPException(status_code=503, detail="服务正在初始化中")
    
    try:
        orders = trading_server.get_orders(
            symbol=symbol,
            status=status,
            start_time=start_time,
            end_time=end_time
        )
        return orders
    except Exception as e:
        logger.error(f"获取订单信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取订单信息失败: {str(e)}")

@app.post("/api/orders")
async def create_order(order_request: OrderRequest):
    """创建订单"""
    if trading_server is None:
        raise HTTPException(status_code=503, detail="服务正在初始化中")
    
    try:
        order_result = trading_server.place_order(
            symbol=order_request.symbol,
            direction=order_request.direction,
            quantity=order_request.quantity,
            price=order_request.price,
            order_type=order_request.order_type,
            strategy_id=order_request.strategy_id
        )
        return order_result
    except Exception as e:
        logger.error(f"创建订单失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建订单失败: {str(e)}")

@app.delete("/api/orders/{order_id}")
async def cancel_order(order_id: str = Path(..., description="订单ID")):
    """取消订单"""
    if trading_server is None:
        raise HTTPException(status_code=503, detail="服务正在初始化中")
    
    try:
        result = trading_server.cancel_order(order_id)
        return result
    except Exception as e:
        logger.error(f"取消订单失败: {e}")
        raise HTTPException(status_code=500, detail=f"取消订单失败: {str(e)}")

@app.get("/api/strategies")
async def get_strategies():
    """获取策略列表"""
    if trading_server is None:
        raise HTTPException(status_code=503, detail="服务正在初始化中")
    
    try:
        strategies = trading_server.get_strategies()
        return strategies
    except Exception as e:
        logger.error(f"获取策略列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取策略列表失败: {str(e)}")

# 启动服务器
def start_server(host: str = "0.0.0.0", port: int = 8002):
    """启动API服务器"""
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    # 从命令行解析参数
    import argparse
    
    parser = argparse.ArgumentParser(description="启动交易API服务器")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8002, help="监听端口")
    parser.add_argument("--interface", type=str, choices=["mock"], 
                        default="mock", help="交易接口类型")
    
    args = parser.parse_args()
    
    # 设置接口类型
    interface_type = args.interface
    
    # 保存配置
    with open("config.json", "w") as f:
        json.dump({"interface_type": interface_type}, f)
    
    # 启动服务器
    logger.info(f"启动API服务器，地址: {args.host}:{args.port}, 接口类型: {interface_type}")
    start_server(args.host, args.port) 