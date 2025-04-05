#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
共享路径和数据存储功能

提供项目中数据文件、信号存储等功能的统一接口
"""

import os
import json
import logging
import requests
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional, List, Union

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shared_paths")

# 项目根目录
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

# 数据目录
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SIGNAL_DIR = os.path.join(DATA_DIR, "signals")
BACKTEST_DIR = os.path.join(DATA_DIR, "backtest")
MARKET_DATA_DIR = os.path.join(DATA_DIR, "market_data")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

# 确保目录存在
for directory in [DATA_DIR, SIGNAL_DIR, BACKTEST_DIR, MARKET_DATA_DIR, LOG_DIR]:
    os.makedirs(directory, exist_ok=True)

def get_data_path(filename: str) -> str:
    """获取数据文件路径"""
    return os.path.join(DATA_DIR, filename)

def get_signal_path(signal_id: str) -> str:
    """获取信号文件路径"""
    return os.path.join(SIGNAL_DIR, f"{signal_id}.json")

def get_backtest_path(backtest_id: str) -> str:
    """获取回测结果文件路径"""
    return os.path.join(BACKTEST_DIR, f"{backtest_id}.json")

def get_market_data_path(symbol: str, period: str, date: str = None) -> str:
    """
    获取市场数据文件路径
    
    参数:
        symbol: 交易品种代码
        period: 数据周期 (tick, 1min, 5min, 15min, 30min, 60min, day, week, month)
        date: 数据日期 (YYYY-MM-DD) 或 None (用于日内数据)
    
    返回:
        文件路径
    """
    symbol_dir = os.path.join(MARKET_DATA_DIR, symbol)
    os.makedirs(symbol_dir, exist_ok=True)
    
    period_dir = os.path.join(symbol_dir, period)
    os.makedirs(period_dir, exist_ok=True)
    
    if date:
        return os.path.join(period_dir, f"{date}.csv")
    else:
        return os.path.join(period_dir, f"history.csv")

def save_dataframe(df: pd.DataFrame, filename: str, directory: str = DATA_DIR) -> str:
    """
    保存DataFrame到CSV文件
    
    参数:
        df: 要保存的DataFrame
        filename: 文件名
        directory: 目录 (默认为数据目录)
    
    返回:
        保存的文件路径
    """
    if not filename.endswith('.csv'):
        filename += '.csv'
        
    filepath = os.path.join(directory, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    df.to_csv(filepath, index=True)
    logger.info(f"DataFrame保存到: {filepath}")
    
    return filepath

def load_dataframe(filename: str, directory: str = DATA_DIR) -> Optional[pd.DataFrame]:
    """
    从CSV文件加载DataFrame
    
    参数:
        filename: 文件名
        directory: 目录 (默认为数据目录)
    
    返回:
        加载的DataFrame或None(如果文件不存在)
    """
    if not filename.endswith('.csv'):
        filename += '.csv'
        
    filepath = os.path.join(directory, filename)
    
    if not os.path.exists(filepath):
        logger.warning(f"文件不存在: {filepath}")
        return None
        
    try:
        df = pd.read_csv(filepath, index_col=0)
        logger.info(f"从 {filepath} 加载DataFrame成功")
        return df
    except Exception as e:
        logger.error(f"加载DataFrame失败: {e}")
        return None

def save_signal(signal_data: Dict[str, Any]) -> str:
    """
    保存交易信号数据
    
    参数:
        signal_data: 信号数据字典
    
    返回:
        保存的文件路径
    """
    if "signal_id" not in signal_data:
        signal_data["signal_id"] = f"signal_{int(datetime.now().timestamp())}"
        
    if "create_time" not in signal_data:
        signal_data["create_time"] = datetime.now().isoformat()
        
    filepath = get_signal_path(signal_data["signal_id"])
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    with open(filepath, 'w') as f:
        json.dump(signal_data, f, indent=2)
        
    logger.info(f"信号数据保存到: {filepath}")
    return filepath

def load_signal(signal_id: str) -> Optional[Dict[str, Any]]:
    """
    加载交易信号数据
    
    参数:
        signal_id: 信号ID
    
    返回:
        信号数据字典或None(如果文件不存在)
    """
    filepath = get_signal_path(signal_id)
    
    if not os.path.exists(filepath):
        logger.warning(f"信号文件不存在: {filepath}")
        return None
        
    try:
        with open(filepath, 'r') as f:
            signal_data = json.load(f)
        
        logger.info(f"从 {filepath} 加载信号数据成功")
        return signal_data
    except Exception as e:
        logger.error(f"加载信号数据失败: {e}")
        return None

def list_signals(strategy_id: Optional[str] = None, start_date: Optional[str] = None, 
                end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    列出符合条件的交易信号
    
    参数:
        strategy_id: 策略ID，如果指定则只列出该策略的信号
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
    
    返回:
        符合条件的信号列表
    """
    signals = []
    
    # 转换日期范围
    start_time = None
    end_time = None
    
    if start_date:
        start_time = datetime.fromisoformat(f"{start_date}T00:00:00")
    
    if end_date:
        end_time = datetime.fromisoformat(f"{end_date}T23:59:59")
    
    # 遍历所有信号文件
    for filename in os.listdir(SIGNAL_DIR):
        if not filename.endswith('.json'):
            continue
            
        filepath = os.path.join(SIGNAL_DIR, filename)
        
        try:
            with open(filepath, 'r') as f:
                signal_data = json.load(f)
                
            # 策略过滤
            if strategy_id and signal_data.get("strategy_id") != strategy_id:
                continue
                
            # 日期过滤
            if start_time or end_time:
                create_time = datetime.fromisoformat(signal_data.get("create_time", "1970-01-01T00:00:00"))
                
                if start_time and create_time < start_time:
                    continue
                    
                if end_time and create_time > end_time:
                    continue
                    
            signals.append(signal_data)
            
        except Exception as e:
            logger.error(f"加载信号文件 {filepath} 失败: {e}")
    
    # 按时间排序
    signals.sort(key=lambda x: x.get("create_time", "1970-01-01T00:00:00"))
    
    return signals

def save_backtest_result(backtest_id: str, result_data: Dict[str, Any]) -> str:
    """
    保存回测结果
    
    参数:
        backtest_id: 回测ID
        result_data: 回测结果数据
    
    返回:
        保存的文件路径
    """
    filepath = get_backtest_path(backtest_id)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    
    # 添加时间戳
    if "timestamp" not in result_data:
        result_data["timestamp"] = datetime.now().isoformat()
        
    with open(filepath, 'w') as f:
        json.dump(result_data, f, indent=2)
        
    logger.info(f"回测结果保存到: {filepath}")
    return filepath

def load_backtest_result(backtest_id: str) -> Optional[Dict[str, Any]]:
    """
    加载回测结果
    
    参数:
        backtest_id: 回测ID
    
    返回:
        回测结果数据字典或None(如果文件不存在)
    """
    filepath = get_backtest_path(backtest_id)
    
    if not os.path.exists(filepath):
        logger.warning(f"回测结果文件不存在: {filepath}")
        return None
        
    try:
        with open(filepath, 'r') as f:
            result_data = json.load(f)
        
        logger.info(f"从 {filepath} 加载回测结果成功")
        return result_data
    except Exception as e:
        logger.error(f"加载回测结果失败: {e}")
        return None

def api_request(endpoint: str, method: str = "GET", params: Optional[Dict[str, Any]] = None, 
               data: Optional[Dict[str, Any]] = None, base_url: str = "http://localhost:8000") -> Any:
    """向API发送请求"""
    url = f"{base_url}{endpoint}"
    
    try:
        if method.upper() == "GET":
            response = requests.get(url, params=params, timeout=10)
        elif method.upper() == "POST":
            response = requests.post(url, params=params, json=data, timeout=10)
        elif method.upper() == "PUT":
            response = requests.put(url, params=params, json=data, timeout=10)
        elif method.upper() == "DELETE":
            response = requests.delete(url, params=params, timeout=10)
        else:
            logger.error(f"不支持的请求方法: {method}")
            return None
        
        # 检查响应状态
        response.raise_for_status()
        
        # 解析JSON响应
        if response.content:
            return response.json()
        return None
    
    except Exception as e:
        logger.error(f"API request error: {e}")
        # 对于测试环境，不抛出异常而是返回None
        return None 