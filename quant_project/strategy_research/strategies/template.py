"""
策略模板基类 - 从LongBridge Quant库集成并适配

提供了基础的策略开发框架，实现了与交易服务器的集成
"""
import logging
import os
import sys
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Union
from datetime import datetime
import pandas as pd
import numpy as np

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

# 导入共享模块
from quant_project.common.shared_paths import save_dataframe, load_dataframe, save_signal, load_signal

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("strategy_template")

class StrategyParam:
    """策略参数基类，支持参数验证"""
    def __init__(self, name: str, value: Any, min_value: Any = None, max_value: Any = None):
        self.name = name
        self.value = value
        self.min_value = min_value
        self.max_value = max_value
        
    def validate(self) -> bool:
        """验证参数是否在有效范围内"""
        if self.min_value is not None and self.value < self.min_value:
            return False
        if self.max_value is not None and self.value > self.max_value:
            return False
        return True
        
    def __str__(self):
        return f"{self.name}={self.value}"


class TickData:
    """Tick数据结构"""
    def __init__(self, symbol: str, timestamp=None, **data):
        self.symbol = symbol
        self.last_price = data.get("price", 0.0)
        self.volume = data.get("volume", 0)
        self.turnover = data.get("turnover", 0.0)
        self.open_price = data.get("open", 0.0)
        self.high_price = data.get("high", 0.0)
        self.low_price = data.get("low", 0.0)
        self.pre_close = data.get("pre_close", 0.0)
        self.timestamp = timestamp or datetime.now()
        
    @classmethod
    def from_quote(cls, symbol: str, quote_data: Dict[str, Any]):
        """从行情数据创建Tick"""
        return cls(
            symbol=symbol,
            timestamp=datetime.fromisoformat(quote_data.get("time", datetime.now().isoformat())),
            price=quote_data.get("price", 0.0),
            volume=quote_data.get("volume", 0),
            turnover=quote_data.get("turnover", 0.0),
            open=quote_data.get("open", 0.0),
            high=quote_data.get("high", 0.0),
            low=quote_data.get("low", 0.0),
            pre_close=quote_data.get("pre_close", 0.0)
        )
        
    def __str__(self):
        return f"Tick({self.symbol}, {self.last_price}, {self.timestamp})"


class BarData:
    """K线数据结构"""
    def __init__(
        self,
        symbol: str,
        open_price: float,
        high_price: float,
        low_price: float,
        close_price: float,
        volume: float,
        turnover: float = 0,
        timestamp: Optional[datetime] = None
    ):
        self.symbol = symbol
        self.open_price = open_price
        self.high_price = high_price
        self.low_price = low_price
        self.close_price = close_price
        self.volume = volume
        self.turnover = turnover
        self.timestamp = timestamp or datetime.now()
        
    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """从字典创建K线"""
        return cls(
            symbol=data['symbol'],
            open_price=data['open'],
            high_price=data['high'],
            low_price=data['low'],
            close_price=data['close'],
            volume=data['volume'],
            turnover=data.get('turnover', 0),
            timestamp=data.get('timestamp')
        )
        
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'symbol': self.symbol,
            'open': self.open_price,
            'high': self.high_price,
            'low': self.low_price,
            'close': self.close_price,
            'volume': self.volume,
            'turnover': self.turnover,
            'timestamp': self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp
        }
        
    def __str__(self):
        return f"Bar({self.symbol}, {self.close_price}, {self.timestamp})"


class StrategyTemplate(ABC):
    """
    策略模板基类
    实现了基本的策略框架和交易服务器接口
    """
    # 策略参数，子类应重写此属性
    parameters = {}
    
    def __init__(
        self,
        strategy_id: str,
        symbols: List[str] = None,
        api_url: str = "http://localhost:8002/api"
    ):
        self.strategy_id = strategy_id
        self.symbols = symbols or []
        self.api_url = api_url
        
        # 交易状态变量
        self.active = False
        self.pos = {symbol: 0 for symbol in self.symbols}
        
        # 应用默认参数
        for name, value in self.parameters.items():
            setattr(self, name, value)
        
        logger.info(f"初始化策略 {self.strategy_id}，交易品种: {self.symbols}")
    
    def start(self):
        """启动策略"""
        if not self.active:
            logger.info(f"启动策略 {self.strategy_id}")
            self.active = True
            self.on_start()
            
    def stop(self):
        """停止策略"""
        if self.active:
            logger.info(f"停止策略 {self.strategy_id}")
            self.active = False
            self.on_stop()
            
    def process_market_data(self, market_data: Dict[str, Dict[str, Any]]):
        """
        处理市场数据更新
        
        参数:
            market_data: 市场数据字典，格式为 {symbol: data}
        """
        if not self.active:
            return
            
        for symbol, data in market_data.items():
            if symbol in self.symbols:
                tick = TickData.from_quote(symbol, data)
                self.on_tick(tick)
                
    def process_bar_data(self, bar_data: Dict[str, Dict[str, Any]]):
        """
        处理K线数据更新
        
        参数:
            bar_data: K线数据字典，格式为 {symbol: data}
        """
        if not self.active:
            return
            
        for symbol, data in bar_data.items():
            if symbol in self.symbols:
                bar = BarData.from_dict(data)
                self.on_bar(bar)
    
    def update_parameters(self, params: Dict[str, Any]):
        """更新策略参数"""
        for name, value in params.items():
            if name in self.parameters:
                logger.info(f"更新参数 {name}: {getattr(self, name)} -> {value}")
                setattr(self, name, value)
                
    def get_parameters(self) -> Dict[str, Any]:
        """获取当前策略参数"""
        return {name: getattr(self, name) for name in self.parameters.keys()}
    
    def create_order(self, symbol: str, direction: str, quantity: int, price: float = None, order_type: str = "limit") -> Dict[str, Any]:
        """
        创建订单
        
        参数:
            symbol: 交易品种
            direction: 交易方向，"buy" 或 "sell"
            quantity: 交易数量
            price: 交易价格，市价单可为None
            order_type: 订单类型，"limit" 或 "market"
            
        返回:
            订单结果
        """
        if not self.active:
            logger.warning(f"策略未激活，无法下单")
            return {"success": False, "error": "策略未激活"}
            
        # 生成交易信号
        signal_data = {
            "signal_id": f"{self.strategy_id}_{int(datetime.now().timestamp())}",
            "strategy_id": self.strategy_id,
            "symbol": symbol,
            "direction": direction,
            "price": price,
            "quantity": quantity,
            "order_type": order_type,
            "reason": f"{self.strategy_id}策略信号",
            "create_time": datetime.now().isoformat()
        }
        
        # 保存信号
        save_signal(signal_data)
        
        # 发送到交易服务器
        import requests
        try:
            response = requests.post(
                f"{self.api_url}/orders",
                json={
                    "symbol": symbol,
                    "direction": direction,
                    "quantity": quantity,
                    "price": price,
                    "order_type": order_type,
                    "strategy_id": self.strategy_id
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                # 更新本地仓位跟踪（乐观更新）
                if direction == "buy":
                    self.pos[symbol] = self.pos.get(symbol, 0) + quantity
                else:
                    self.pos[symbol] = self.pos.get(symbol, 0) - quantity
                    
                logger.info(f"下单成功: {symbol} {direction} {quantity}股 @ {price if price else '市价'}")
                return result
            else:
                logger.error(f"下单失败: {response.text}")
                return {"success": False, "error": response.text}
                
        except Exception as e:
            logger.error(f"下单异常: {e}")
            return {"success": False, "error": str(e)}
    
    def buy(self, symbol: str, quantity: int, price: float = None, order_type: str = "limit") -> Dict[str, Any]:
        """买入"""
        return self.create_order(symbol, "buy", quantity, price, order_type)
        
    def sell(self, symbol: str, quantity: int, price: float = None, order_type: str = "limit") -> Dict[str, Any]:
        """卖出"""
        return self.create_order(symbol, "sell", quantity, price, order_type)
    
    def get_position(self, symbol: str) -> int:
        """获取当前持仓"""
        # 尝试从交易服务器获取最新持仓
        import requests
        try:
            response = requests.get(f"{self.api_url}/positions?symbol={symbol}")
            if response.status_code == 200:
                positions = response.json()
                for pos in positions:
                    if pos["symbol"] == symbol:
                        quantity = pos["quantity"]
                        # 更新本地跟踪
                        self.pos[symbol] = quantity
                        return quantity
                        
            # 如果API请求失败或没有持仓，返回本地跟踪的持仓
            return self.pos.get(symbol, 0)
            
        except Exception:
            # 出错时返回本地跟踪的持仓
            return self.pos.get(symbol, 0)
    
    def get_market_data(self, symbols: List[str] = None) -> Dict[str, Dict[str, Any]]:
        """获取市场数据"""
        if symbols is None:
            symbols = self.symbols
            
        import requests
        try:
            symbols_str = ",".join(symbols)
            response = requests.get(f"{self.api_url}/quotes?symbols={symbols_str}")
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"获取市场数据失败: {response.text}")
                return {}
        except Exception as e:
            logger.error(f"获取市场数据异常: {e}")
            return {}
    
    @abstractmethod
    def on_tick(self, tick: TickData):
        """
        处理Tick数据
        子类必须实现此方法
        """
        pass
        
    def on_bar(self, bar: BarData):
        """处理K线数据"""
        pass
        
    def on_start(self):
        """策略启动时调用"""
        pass
        
    def on_stop(self):
        """策略停止时调用"""
        pass
        
    def __str__(self):
        """策略描述"""
        params = ", ".join(f"{k}={getattr(self, k)}" for k in self.parameters.keys())
        return f"{self.__class__.__name__}({self.strategy_id}, {params})" 