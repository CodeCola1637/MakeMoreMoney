#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
量化交易系统 - 数据服务层 API

此模块提供数据服务 API，包括：
1. 行情数据查询接口
2. 基本面数据查询接口
3. 交易信号存取接口
4. 系统状态监控接口

使用方法：
    conda activate data_service_env
    python api_server.py
"""

import os
import sys
import json
import logging
import datetime
import time
from typing import List, Dict, Any, Optional, Union

# 添加项目根目录到 sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 导入共享模块
from common.shared_paths import (
    save_dataframe, load_dataframe, save_signal, load_signal,
    LOG_DIR, DATA_DIR, SIGNAL_DIR
)

# 导入 FastAPI 相关库
from fastapi import FastAPI, HTTPException, Query, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

# 导入其他必要库
import pandas as pd
import numpy as np
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# 尝试导入 dolphindb
try:
    import dolphindb as ddb
    DOLPHINDB_AVAILABLE = True
except ImportError:
    DOLPHINDB_AVAILABLE = False
    print("警告: 无法导入 dolphindb 模块，将使用模拟数据")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, f"data_service_{datetime.datetime.now().strftime('%Y%m%d')}.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("data_service")

# 加载环境变量
load_dotenv()

# ======== 数据模型定义 ========

class StockInfo(BaseModel):
    code: str
    name: str = ""
    exchange: str = ""
    industry: str = ""
    list_date: Optional[str] = None

class StockDailyBar(BaseModel):
    code: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: Optional[float] = None
    adj_factor: Optional[float] = None

class SignalData(BaseModel):
    name: str
    date: str = Field(default_factory=lambda: datetime.datetime.now().strftime('%Y%m%d'))
    type: str = "trade"
    data: List[Dict[str, Any]]
    meta: Optional[Dict[str, Any]] = None

class SystemStatus(BaseModel):
    status: str = "running"
    version: str = "1.0.0"
    start_time: str
    uptime_seconds: int
    data_sources: Dict[str, str]
    memory_usage_mb: float = 0
    cpu_usage_percent: float = 0

# ======== 数据提供者 ========

class DataProvider:
    """数据提供者基类"""
    
    def __init__(self):
        self.start_time = datetime.datetime.now()
        logger.info(f"初始化 {self.__class__.__name__}")
    
    def get_stock_list(self) -> List[Dict[str, Any]]:
        """获取股票列表"""
        raise NotImplementedError
    
    def get_stock_daily(self, code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """获取股票日线数据"""
        raise NotImplementedError
    
    def get_system_status(self) -> Dict[str, Any]:
        """获取系统状态"""
        uptime = (datetime.datetime.now() - self.start_time).total_seconds()
        
        status = {
            "status": "running",
            "version": "1.0.0",
            "start_time": self.start_time.isoformat(),
            "uptime_seconds": int(uptime),
            "data_sources": self.get_data_source_info()
        }
        
        # 尝试获取资源使用情况
        try:
            import psutil
            process = psutil.Process(os.getpid())
            status["memory_usage_mb"] = process.memory_info().rss / (1024 * 1024)
            status["cpu_usage_percent"] = process.cpu_percent(interval=0.1)
        except ImportError:
            pass
            
        return status
    
    def get_data_source_info(self) -> Dict[str, str]:
        """获取数据源信息"""
        return {"mock": "active"}

class DolphinDBProvider(DataProvider):
    """DolphinDB 数据提供者"""
    
    def __init__(self):
        super().__init__()
        self.conn = None
        self.connect()
    
    def connect(self):
        """连接到 DolphinDB 数据库"""
        if not DOLPHINDB_AVAILABLE:
            logger.warning("DolphinDB 库不可用，无法连接")
            return
        
        try:
            # 从环境变量获取连接信息
            host = os.getenv("DOLPHINDB_HOST", "localhost")
            port = int(os.getenv("DOLPHINDB_PORT", "8848"))
            username = os.getenv("DOLPHINDB_USER", "admin")
            password = os.getenv("DOLPHINDB_PASS", "123456")
            
            # 连接到服务器
            self.conn = ddb.session()
            self.conn.connect(host, port, username, password)
            logger.info(f"成功连接到 DolphinDB 服务器 {host}:{port}")
        except Exception as e:
            logger.error(f"连接 DolphinDB 失败: {e}")
            self.conn = None
    
    def get_stock_list(self) -> List[Dict[str, Any]]:
        """获取股票列表"""
        if not self.conn:
            return self._get_mock_stock_list()
        
        try:
            # 执行 DolphinDB 查询
            query = "select distinct code, name, exchange, industry, list_date from loadTable('dfs://market', 'stocks')"
            data = self.conn.run(query)
            
            # 转换为列表字典
            if isinstance(data, list):
                return data
            else:
                df = pd.DataFrame(data)
                return df.to_dict(orient="records")
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return self._get_mock_stock_list()
    
    def _get_mock_stock_list(self) -> List[Dict[str, Any]]:
        """获取模拟股票列表"""
        return [
            {"code": "000001.SZ", "name": "平安银行", "exchange": "SZSE", "industry": "银行", "list_date": "19910403"},
            {"code": "600000.SH", "name": "浦发银行", "exchange": "SSE", "industry": "银行", "list_date": "19991110"},
            {"code": "601398.SH", "name": "工商银行", "exchange": "SSE", "industry": "银行", "list_date": "20061027"},
            {"code": "00700.HK", "name": "腾讯控股", "exchange": "HKEX", "industry": "互联网", "list_date": "20040616"},
            {"code": "09988.HK", "name": "阿里巴巴", "exchange": "HKEX", "industry": "互联网", "list_date": "20191126"},
            {"code": "AAPL.US", "name": "苹果公司", "exchange": "NASDAQ", "industry": "科技", "list_date": "19801212"},
            {"code": "MSFT.US", "name": "微软", "exchange": "NASDAQ", "industry": "科技", "list_date": "19860313"},
        ]
    
    def get_stock_daily(self, code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """获取股票日线数据"""
        if not self.conn:
            return self._get_mock_stock_daily(code, start_date, end_date)
        
        try:
            # 构建查询
            query = f"""
            select date, open, high, low, close, volume, amount, adj_factor 
            from loadTable('dfs://market', 'daily') 
            where code='{code}'
            """
            
            if start_date:
                query += f" and date>='{start_date}'"
            if end_date:
                query += f" and date<='{end_date}'"
            
            query += " order by date"
            
            # 执行查询
            data = self.conn.run(query)
            df = pd.DataFrame(data)
            
            # 确保日期格式正确
            if 'date' in df.columns and not pd.api.types.is_datetime64_any_dtype(df['date']):
                df['date'] = pd.to_datetime(df['date'])
            
            return df
        except Exception as e:
            logger.error(f"获取股票日线数据失败: {e}")
            return self._get_mock_stock_daily(code, start_date, end_date)
    
    def _get_mock_stock_daily(self, code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """获取模拟股票日线数据"""
        # 生成日期序列
        if start_date:
            start = pd.Timestamp(start_date)
        else:
            start = pd.Timestamp('2021-01-01')
        
        if end_date:
            end = pd.Timestamp(end_date)
        else:
            end = pd.Timestamp('2023-12-31')
        
        date_range = pd.date_range(start=start, end=end, freq='B')
        
        # 生成模拟价格
        np.random.seed(hash(code) % 10000)
        base_price = 100 + np.random.random() * 400  # 基础价格
        
        # 生成波动序列
        returns = np.random.normal(0.0002, 0.015, size=len(date_range))
        prices = base_price * np.cumprod(1 + returns)
        
        # 创建模拟数据
        df = pd.DataFrame({
            'date': date_range,
            'open': prices * (1 + np.random.normal(0, 0.005, size=len(prices))),
            'high': prices * (1 + np.abs(np.random.normal(0, 0.01, size=len(prices)))),
            'low': prices * (1 - np.abs(np.random.normal(0, 0.01, size=len(prices)))),
            'close': prices,
            'volume': np.random.randint(1000000, 10000000, size=len(prices)),
            'amount': prices * np.random.randint(1000000, 10000000, size=len(prices)) / 100,
            'adj_factor': np.ones(len(prices))
        })
        
        # 确保高低价是合理的
        df['high'] = df[['open', 'high', 'close']].max(axis=1)
        df['low'] = df[['open', 'low', 'close']].min(axis=1)
        
        return df
    
    def get_data_source_info(self) -> Dict[str, str]:
        """获取数据源信息"""
        if self.conn:
            try:
                # 获取 DolphinDB 版本
                version = self.conn.run("version()")
                return {"dolphindb": f"active (version {version})"}
            except:
                return {"dolphindb": "connected"}
        else:
            return {"dolphindb": "unavailable", "mock": "active"}

# ======== API 应用 ========

# 创建数据提供者实例
data_provider = DolphinDBProvider()

# 创建 FastAPI 应用
app = FastAPI(
    title="量化交易系统 - 数据服务 API",
    description="提供行情数据、基本面数据、交易信号等服务的 API",
    version="1.0.0"
)

# 允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======== API 路由 ========

@app.get("/")
def read_root():
    """API 根路径"""
    return {
        "name": "量化交易系统 - 数据服务 API",
        "version": "1.0.0",
        "status": "running",
        "docs_url": "/docs"
    }

@app.get("/api/status")
def get_status() -> SystemStatus:
    """获取系统状态"""
    return data_provider.get_system_status()

@app.get("/api/stocks")
def get_stocks() -> List[StockInfo]:
    """获取股票列表"""
    return data_provider.get_stock_list()

@app.get("/api/stocks/{code}/daily")
def get_stock_daily(
    code: str,
    start_date: Optional[str] = Query(None, description="开始日期，格式: YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期，格式: YYYY-MM-DD"),
    format: Optional[str] = Query("json", description="返回格式: json, csv")
):
    """
    获取股票日线数据
    
    - **code**: 股票代码，例如: 000001.SZ, 600000.SH, AAPL.US
    - **start_date**: 开始日期，格式: YYYY-MM-DD
    - **end_date**: 结束日期，格式: YYYY-MM-DD
    - **format**: 返回格式: json, csv
    """
    try:
        df = data_provider.get_stock_daily(code, start_date, end_date)
        
        # 根据请求格式返回数据
        if format.lower() == "csv":
            csv_data = df.to_csv(index=False)
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(
                content=csv_data,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={code}_daily.csv"}
            )
        else:
            # 转换为字典列表
            # 处理日期格式
            if 'date' in df.columns:
                df['date'] = df['date'].dt.strftime('%Y-%m-%d')
            
            return df.to_dict(orient="records")
    except Exception as e:
        logger.error(f"处理股票日线数据请求失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/signals")
def save_signal_data(signal: SignalData, background_tasks: BackgroundTasks):
    """保存交易信号数据"""
    try:
        # 转换为 DataFrame
        signal_df = pd.DataFrame(signal.data)
        
        # 在后台任务中保存信号
        def save_signal_task():
            path = save_signal(signal_df, signal.name, signal.type, signal.date)
            logger.info(f"信号数据已保存: {path}")
        
        background_tasks.add_task(save_signal_task)
        
        return {"status": "success", "message": "信号数据已接收并开始处理"}
    except Exception as e:
        logger.error(f"保存信号数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/signals/{name}")
def get_signal_data(
    name: str,
    type: str = Query("trade", description="信号类型: trade, factor, portfolio"),
    date: Optional[str] = Query(None, description="日期，格式: YYYYMMDD"),
    latest: bool = Query(False, description="是否获取最新信号")
):
    """获取交易信号数据"""
    try:
        signal_df = load_signal(name, type, date, latest)
        
        # 处理日期列
        date_cols = [col for col in signal_df.columns if col.lower().endswith('date')]
        for col in date_cols:
            if pd.api.types.is_datetime64_any_dtype(signal_df[col]):
                signal_df[col] = signal_df[col].dt.strftime('%Y-%m-%d')
        
        return signal_df.to_dict(orient="records")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"信号 {name} 不存在")
    except Exception as e:
        logger.error(f"获取信号数据失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ======== 主程序 ========

if __name__ == "__main__":
    # 获取端口
    port = int(os.getenv("DATA_SERVICE_PORT", "8000"))
    
    # 启动服务
    logger.info(f"数据服务 API 启动在端口 {port}")
    uvicorn.run("api_server:app", host="0.0.0.0", port=port, reload=True) 