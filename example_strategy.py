#!/usr/bin/env python3
"""
使用本地模拟数据的简单交易策略示例
演示如何在无法连接到真实API时开发和测试交易策略
"""

import os
import logging
import time
from local_mock_data import LongPortAPIMock
from typing import Dict, List, Union, Optional
import json

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('trading_strategy')

class SimpleMAStrategy:
    """
    简单移动平均线交易策略
    - 当短期均线上穿长期均线时买入
    - 当短期均线下穿长期均线时卖出
    """
    
    def __init__(self, api: LongPortAPIMock, short_period: int = 5, long_period: int = 20):
        self.api = api
        self.short_period = short_period
        self.long_period = long_period
        self.watch_list = ["700.HK", "9988.HK", "AAPL.US", "TSLA.US"]
        
        # 初始化持仓信息
        self.position_info = {}
        self.update_position_info()
        
        # 策略状态记录
        self.strategy_state = {}
        for symbol in self.watch_list:
            self.strategy_state[symbol] = {
                "last_signal": None,  # BUY, SELL or None
                "last_price": None,
                "ma_short": None,
                "ma_long": None
            }
    
    def update_position_info(self):
        """更新持仓信息"""
        account_balance = self.api.get_account_balance()
        self.cash_balance = account_balance["cash"]["available_balance"]
        
        # 更新持仓
        self.position_info = {}
        for holding in account_balance["holdings"]:
            self.position_info[holding["symbol"]] = holding
        
        logger.info(f"账户余额: {self.cash_balance:.2f}, 持仓数量: {len(self.position_info)}")
    
    def calculate_ma(self, symbol: str) -> bool:
        """计算移动平均线并返回是否成功"""
        try:
            # 获取K线数据 (需要比长期均线周期更长的历史数据)
            candles = self.api.get_candlesticks(symbol, "1d", self.long_period + 10)
            
            if isinstance(candles, dict) and "error" in candles:
                logger.error(f"获取{symbol}K线数据失败: {candles['error']}")
                return False
            
            if len(candles) < self.long_period:
                logger.warning(f"{symbol}的历史数据不足，无法计算均线")
                return False
            
            # 提取收盘价
            close_prices = [candle["close"] for candle in candles]
            
            # 计算短期移动平均线
            ma_short = sum(close_prices[-self.short_period:]) / self.short_period
            
            # 计算长期移动平均线
            ma_long = sum(close_prices[-self.long_period:]) / self.long_period
            
            # 更新状态
            self.strategy_state[symbol]["last_price"] = close_prices[-1]
            self.strategy_state[symbol]["ma_short"] = ma_short
            self.strategy_state[symbol]["ma_long"] = ma_long
            
            logger.info(f"{symbol} - 价格: {close_prices[-1]:.2f}, 短期MA: {ma_short:.2f}, 长期MA: {ma_long:.2f}")
            return True
            
        except Exception as e:
            logger.error(f"计算{symbol}均线时出错: {str(e)}")
            return False
    
    def check_buy_signal(self, symbol: str) -> bool:
        """检查买入信号"""
        state = self.strategy_state[symbol]
        
        # 如果没有计算出均线数据，无法产生信号
        if state["ma_short"] is None or state["ma_long"] is None:
            return False
        
        # 如果已有买入信号，不重复买入
        if state["last_signal"] == "BUY":
            return False
        
        # 检查短期均线是否上穿长期均线 (金叉)
        if state["ma_short"] > state["ma_long"]:
            # 如果之前没有信号或者是卖出信号，现在产生了新的买入信号
            if state["last_signal"] is None or state["last_signal"] == "SELL":
                logger.info(f"{symbol} 产生买入信号! 短期MA {state['ma_short']:.2f} > 长期MA {state['ma_long']:.2f}")
                state["last_signal"] = "BUY"
                return True
        
        return False
    
    def check_sell_signal(self, symbol: str) -> bool:
        """检查卖出信号"""
        state = self.strategy_state[symbol]
        
        # 如果没有计算出均线数据，无法产生信号
        if state["ma_short"] is None or state["ma_long"] is None:
            return False
        
        # 如果已有卖出信号或没有买入过，不产生卖出信号
        if state["last_signal"] == "SELL" or state["last_signal"] is None:
            return False
        
        # 检查短期均线是否下穿长期均线 (死叉)
        if state["ma_short"] < state["ma_long"]:
            logger.info(f"{symbol} 产生卖出信号! 短期MA {state['ma_short']:.2f} < 长期MA {state['ma_long']:.2f}")
            state["last_signal"] = "SELL"
            return True
        
        return False
    
    def execute_buy(self, symbol: str) -> bool:
        """执行买入操作"""
        # 获取最新报价
        quote = self.api.get_stock_quote(symbol)
        
        if isinstance(quote, dict) and "error" in quote:
            logger.error(f"获取{symbol}报价失败: {quote['error']}")
            return False
        
        current_price = quote["last_done"]
        lot_size = quote["lot_size"]
        
        # 计算可以买入的数量 (使用50%的可用资金)
        available_cash = self.cash_balance * 0.5
        max_quantity = int((available_cash / current_price) // lot_size * lot_size)
        
        if max_quantity < lot_size:
            logger.warning(f"资金不足，无法买入{symbol} (最小手数: {lot_size})")
            return False
        
        # 下单
        order = self.api.place_order(
            symbol=symbol,
            quantity=max_quantity,
            side="BUY",
            order_type="LIMIT",
            price=current_price
        )
        
        if "error" in order:
            logger.error(f"买入{symbol}失败: {order['error']}")
            return False
        
        logger.info(f"买入{symbol}成功! 订单ID: {order['order_id']}, 数量: {max_quantity}, 价格: {current_price:.2f}")
        
        # 更新持仓信息
        self.update_position_info()
        return True
    
    def execute_sell(self, symbol: str) -> bool:
        """执行卖出操作"""
        # 检查是否持有股票
        if symbol not in self.position_info:
            logger.warning(f"未持有{symbol}，无法卖出")
            return False
        
        holding = self.position_info[symbol]
        quantity = holding["quantity"]
        
        # 获取最新报价
        quote = self.api.get_stock_quote(symbol)
        
        if isinstance(quote, dict) and "error" in quote:
            logger.error(f"获取{symbol}报价失败: {quote['error']}")
            return False
        
        current_price = quote["last_done"]
        
        # 下单
        order = self.api.place_order(
            symbol=symbol,
            quantity=quantity,
            side="SELL",
            order_type="LIMIT",
            price=current_price
        )
        
        if "error" in order:
            logger.error(f"卖出{symbol}失败: {order['error']}")
            return False
        
        logger.info(f"卖出{symbol}成功! 订单ID: {order['order_id']}, 数量: {quantity}, 价格: {current_price:.2f}")
        
        # 更新持仓信息
        self.update_position_info()
        return True
    
    def run_iteration(self):
        """运行一次策略迭代"""
        logger.info("开始策略迭代...")
        
        # 更新持仓信息
        self.update_position_info()
        
        # 遍历关注的股票
        for symbol in self.watch_list:
            # 计算均线
            if not self.calculate_ma(symbol):
                continue
            
            # 检查买入信号
            if self.check_buy_signal(symbol):
                self.execute_buy(symbol)
            
            # 检查卖出信号
            elif self.check_sell_signal(symbol) and symbol in self.position_info:
                self.execute_sell(symbol)
        
        logger.info("策略迭代完成")
    
    def run_backtest(self, days: int = 30):
        """简单回测"""
        logger.info(f"开始回测 ({days}天)...")
        
        # 初始资金
        initial_cash = self.cash_balance
        
        # 每天运行一次策略
        for day in range(1, days + 1):
            logger.info(f"========== 回测第 {day} 天 ==========")
            self.run_iteration()
            
            # 休息一下避免日志过多
            time.sleep(0.1)
        
        # 计算最终资产
        final_cash = self.cash_balance
        total_value = final_cash
        
        for symbol, position in self.position_info.items():
            total_value += position["quantity"] * position["current_price"]
        
        profit = total_value - initial_cash
        profit_pct = (profit / initial_cash) * 100
        
        logger.info("========== 回测结果 ==========")
        logger.info(f"初始资金: {initial_cash:.2f}")
        logger.info(f"最终现金: {final_cash:.2f}")
        logger.info(f"持仓市值: {(total_value - final_cash):.2f}")
        logger.info(f"总资产: {total_value:.2f}")
        logger.info(f"盈亏: {profit:.2f} ({profit_pct:.2f}%)")
        
        return {
            "initial_cash": initial_cash,
            "final_cash": final_cash,
            "total_value": total_value,
            "profit": profit,
            "profit_pct": profit_pct
        }


if __name__ == "__main__":
    logger.info("开始运行策略...")
    
    # 创建API客户端
    api = LongPortAPIMock()
    
    # 创建策略实例
    strategy = SimpleMAStrategy(api, short_period=5, long_period=20)
    
    # 运行策略回测
    results = strategy.run_backtest(days=30)
    
    # 输出最终结果
    print("\n========== 策略回测报告 ==========")
    print(f"初始资金: {results['initial_cash']:.2f}")
    print(f"最终资产: {results['total_value']:.2f}")
    print(f"总收益率: {results['profit_pct']:.2f}%")
    
    print("\n策略运行完成") 