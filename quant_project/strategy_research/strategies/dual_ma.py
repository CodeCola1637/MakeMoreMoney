"""
双均线交叉策略

经典的双均线交叉策略，从longbridge_quant库集成并适配到quant_project结构:
- 当快速均线上穿慢速均线时买入
- 当快速均线下穿慢速均线时卖出
"""
import logging
import os
import sys
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
from collections import deque
from datetime import datetime, timedelta

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

# 导入策略模板
from quant_project.strategy_research.strategies.template import StrategyTemplate, TickData, BarData

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dual_ma_strategy")

class DualMAStrategy(StrategyTemplate):
    """
    双均线交叉策略
    - 当快速均线上穿慢速均线时买入
    - 当快速均线下穿慢速均线时卖出
    """
    
    parameters = {
        "fast_window": 5,        # 快速均线窗口
        "slow_window": 20,       # 慢速均线窗口
        "order_volume": 100,     # 下单数量
        "price_add_pct": 0.001,  # 价格调整百分比
        "ma_type": "simple",     # 均线类型："simple" 或 "exponential"
        "initial_lookback": 30   # 初始数据加载条数
    }
    
    def __init__(
        self,
        strategy_id: str,
        symbols: List[str],
        api_url: str = "http://localhost:8002/api"
    ):
        super().__init__(strategy_id, symbols, api_url)
        
        # 初始化交易数据字典
        self.trading_data = {}
        
        # 初始化每个交易品种的数据结构
        for symbol in self.symbols:
            self.trading_data[symbol] = {
                "fast_ma": 0.0,              # 快速均线当前值
                "slow_ma": 0.0,              # 慢速均线当前值
                "last_price": 0.0,           # 最新价格
                "bars": deque(maxlen=max(self.slow_window, self.fast_window) + 10),  # K线缓存
                "prev_fast_ma": 0.0,         # 前一周期快速均线值
                "prev_slow_ma": 0.0,         # 前一周期慢速均线值
                "position": 0,               # 当前持仓
                "last_order_time": None,     # 最后下单时间
                "highest_price_since_entry": 0.0,  # 入场后最高价，用于追踪止损
                "lowest_price_since_entry": float('inf'),  # 入场后最低价，用于追踪止损
                "entry_price": 0.0           # 入场价格
            }
    
    def on_start(self):
        """策略启动时调用，加载历史数据初始化均线"""
        logger.info(f"正在初始化双均线策略 {self.strategy_id}...")
        
        # 加载历史数据计算初始均线
        for symbol in self.symbols:
            self._initialize_ma_data(symbol)
            
        logger.info(f"双均线策略 {self.strategy_id} 初始化完成")
        
    def _initialize_ma_data(self, symbol: str):
        """初始化均线数据"""
        try:
            # 尝试从数据服务获取历史K线
            import requests
            
            response = requests.get(
                f"{self.api_url}/history",
                params={
                    "symbol": symbol,
                    "period": "day",
                    "count": self.initial_lookback
                }
            )
            
            if response.status_code != 200:
                logger.warning(f"无法获取 {symbol} 的历史数据，使用空数据初始化")
                return
                
            bars_data = response.json()
            
            if not bars_data:
                logger.warning(f"未找到 {symbol} 的历史数据")
                return
                
            # 转换为Bar对象并加入缓存
            for bar_dict in bars_data:
                bar = BarData.from_dict(bar_dict)
                self.trading_data[symbol]["bars"].append(bar)
                
            # 计算初始均线
            self._update_moving_averages(symbol)
            
            # 设置前一周期值
            self.trading_data[symbol]["prev_fast_ma"] = self.trading_data[symbol]["fast_ma"]
            self.trading_data[symbol]["prev_slow_ma"] = self.trading_data[symbol]["slow_ma"]
            
            logger.info(
                f"初始化 {symbol} 均线数据成功: "
                f"快速({self.fast_window})={self.trading_data[symbol]['fast_ma']:.2f}, "
                f"慢速({self.slow_window})={self.trading_data[symbol]['slow_ma']:.2f}"
            )
            
        except Exception as e:
            logger.error(f"初始化 {symbol} 均线数据失败: {e}")
            
    def on_tick(self, tick: TickData):
        """处理Tick数据"""
        symbol = tick.symbol
        
        if symbol not in self.trading_data:
            return
            
        # 更新最新价格
        self.trading_data[symbol]["last_price"] = tick.last_price
        
        # 检查风险管理（追踪止损）
        self._check_risk_management(symbol, tick.last_price)
            
    def on_bar(self, bar: BarData):
        """处理K线数据并执行交易决策"""
        symbol = bar.symbol
        
        if symbol not in self.trading_data:
            return
            
        # 添加K线到历史数据
        self.trading_data[symbol]["bars"].append(bar)
        
        # 更新均线
        self._update_moving_averages(symbol)
        
        # 检查交易信号
        self._check_trading_signals(symbol)
        
        # 记录最新价格
        self.trading_data[symbol]["last_price"] = bar.close_price
        
    def _update_moving_averages(self, symbol: str):
        """更新均线"""
        data = self.trading_data[symbol]
        bars = data["bars"]
        
        if len(bars) < self.fast_window:
            return
            
        # 保存前一周期值
        data["prev_fast_ma"] = data["fast_ma"]
        data["prev_slow_ma"] = data["slow_ma"]
        
        # 计算收盘价序列
        closes = [bar.close_price for bar in bars]
        
        # 根据均线类型计算
        if self.ma_type == "simple":
            # 简单移动平均
            data["fast_ma"] = np.mean(closes[-self.fast_window:])
            if len(closes) >= self.slow_window:
                data["slow_ma"] = np.mean(closes[-self.slow_window:])
        else:
            # 指数移动平均
            data["fast_ma"] = self._calculate_ema(closes, self.fast_window)
            if len(closes) >= self.slow_window:
                data["slow_ma"] = self._calculate_ema(closes, self.slow_window)
                
    def _calculate_ema(self, prices: List[float], window: int) -> float:
        """计算指数移动平均"""
        if len(prices) < window:
            return 0.0
            
        weights = np.exp(np.linspace(-1., 0., window))
        weights /= weights.sum()
        
        ema = np.average(
            prices[-window:],
            weights=weights
        )
        return ema
        
    def _check_trading_signals(self, symbol: str):
        """检查并执行交易信号"""
        data = self.trading_data[symbol]
        
        # 如果均线还未计算完成，则跳过
        if not data["fast_ma"] or not data["slow_ma"] or not data["prev_fast_ma"] or not data["prev_slow_ma"]:
            return
            
        # 获取均线值
        fast_ma = data["fast_ma"]
        slow_ma = data["slow_ma"]
        prev_fast_ma = data["prev_fast_ma"]
        prev_slow_ma = data["prev_slow_ma"]
        
        # 获取当前价格和持仓
        current_price = data["last_price"]
        current_position = self.get_position(symbol)
        
        # 检查金叉（快线上穿慢线）
        bullish_crossover = prev_fast_ma <= prev_slow_ma and fast_ma > slow_ma
        
        # 检查死叉（快线下穿慢线）
        bearish_crossover = prev_fast_ma >= prev_slow_ma and fast_ma < slow_ma
        
        # 交易逻辑
        if bullish_crossover and current_position <= 0:
            # 买入信号
            buy_price = current_price * (1 + self.price_add_pct)  # 加一点点价格增加成交概率
            self.buy(
                symbol=symbol,
                quantity=self.order_volume,
                price=buy_price,
                order_type="limit"
            )
            
            # 记录入场价格
            data["entry_price"] = current_price
            data["highest_price_since_entry"] = current_price
            data["lowest_price_since_entry"] = current_price
            data["last_order_time"] = datetime.now()
            
            logger.info(
                f"买入信号 {symbol}: 快线({fast_ma:.2f}) 上穿 慢线({slow_ma:.2f}), "
                f"买入 {self.order_volume} 股，价格 {buy_price:.2f}"
            )
            
        elif bearish_crossover and current_position > 0:
            # 卖出信号
            sell_price = current_price * (1 - self.price_add_pct)  # 降低一点点价格增加成交概率
            self.sell(
                symbol=symbol,
                quantity=min(self.order_volume, current_position),
                price=sell_price,
                order_type="limit"
            )
            
            data["last_order_time"] = datetime.now()
            
            logger.info(
                f"卖出信号 {symbol}: 快线({fast_ma:.2f}) 下穿 慢线({slow_ma:.2f}), "
                f"卖出 {min(self.order_volume, current_position)} 股，价格 {sell_price:.2f}"
            )
    
    def _check_risk_management(self, symbol: str, current_price: float):
        """风险管理检查，包括追踪止损"""
        data = self.trading_data[symbol]
        current_position = self.get_position(symbol)
        
        if current_position <= 0:
            return
            
        # 更新最高/最低价格
        if current_price > data["highest_price_since_entry"]:
            data["highest_price_since_entry"] = current_price
            
        if current_price < data["lowest_price_since_entry"]:
            data["lowest_price_since_entry"] = current_price
            
        # 追踪止损: 价格从最高点回落超过10%
        if data["highest_price_since_entry"] > 0:
            drawdown = (data["highest_price_since_entry"] - current_price) / data["highest_price_since_entry"]
            
            if drawdown >= 0.1:  # 10% 回撤止损
                # 市价卖出
                self.sell(
                    symbol=symbol,
                    quantity=current_position,
                    price=None,
                    order_type="market"
                )
                
                logger.info(
                    f"追踪止损 {symbol}: 从高点 {data['highest_price_since_entry']:.2f} "
                    f"回撤 {drawdown:.1%} 到 {current_price:.2f}, 市价卖出 {current_position} 股"
                )
    
    def generate_stats(self, symbol: str) -> Dict[str, Any]:
        """生成策略统计数据"""
        data = self.trading_data[symbol]
        
        return {
            "symbol": symbol,
            "fast_ma": data["fast_ma"],
            "slow_ma": data["slow_ma"],
            "current_price": data["last_price"],
            "position": self.get_position(symbol),
            "entry_price": data["entry_price"],
            "highest_since_entry": data["highest_price_since_entry"],
            "lowest_since_entry": data["lowest_price_since_entry"],
            "unrealized_pnl_pct": ((data["last_price"] / data["entry_price"]) - 1) * 100 if data["entry_price"] > 0 else 0,
            "update_time": datetime.now().isoformat()
        } 