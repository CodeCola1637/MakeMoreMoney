"""
Dual Moving Average strategy implementation
"""
import logging
import os
from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal

from longport.openapi import OrderSide, OrderType, Period, AdjustType

from .template import CtaTemplate, TickData, BarData
from ..api_client.client import LongPortClient
from ..data_engine.realtime import QuoteProcessor
from ..data_engine.historical import HistoricalDataLoader

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dual_ma_strategy")

class DualMAStrategy(CtaTemplate):
    """
    Classic dual moving average crossover strategy
    - Buy when fast MA crosses above slow MA
    - Sell when fast MA crosses below slow MA
    """
    
    parameters = {
        "fast_window": 5,
        "slow_window": 20,
        "order_volume": 100,
        "price_add_pct": 0.001,  # Add 0.1% to buy price, subtract from sell price
        "ma_type": "simple",     # "simple" or "exponential"
        "initial_lookback": 100  # Initial bars to load for MA calculation
    }
    
    def __init__(
        self,
        client: LongPortClient,
        quote_processor: QuoteProcessor,
        symbols: List[str],
        historical_loader: Optional[HistoricalDataLoader] = None
    ):
        super().__init__(client, quote_processor, symbols)
        
        # Historical data loader
        self.historical_loader = historical_loader or HistoricalDataLoader(client)
        
        # Trading state
        self.trading_data: Dict[str, dict] = {}
        
        # Initialize data structures for each symbol
        for symbol in self.symbols:
            self.trading_data[symbol] = {
                "fast_ma": 0.0,
                "slow_ma": 0.0,
                "last_price": 0.0,
                "bars": deque(maxlen=self.slow_window + 10),
                "prev_fast_ma": 0.0,
                "prev_slow_ma": 0.0,
                "position": 0,
                "last_order_time": None,
                "highest_price_since_entry": 0.0,
                "lowest_price_since_entry": float('inf'),
                "entry_price": 0.0
            }
        
    def on_start(self):
        """Initialize strategy with historical data on start"""
        logger.info("Initializing Dual MA Strategy...")
        
        # Load historical data and calculate initial MAs
        for symbol in self.symbols:
            self._initialize_ma_data(symbol)
            
        logger.info("Dual MA Strategy initialized successfully")
        
    def _initialize_ma_data(self, symbol: str):
        """Initialize moving averages with historical data"""
        try:
            # Load historical bars
            bars_df = self.historical_loader.get_bars(
                symbol=symbol,
                period=Period.Day,
                count=self.initial_lookback,
                adjust_type=AdjustType.NoAdjust
            )
            
            if bars_df.empty:
                logger.warning(f"No historical data for {symbol}, cannot initialize")
                return
                
            # Convert to bar data and calculate MAs
            bars = []
            for _, row in bars_df.iterrows():
                bar = BarData(
                    symbol=symbol,
                    open_price=row['open'],
                    high_price=row['high'],
                    low_price=row['low'],
                    close_price=row['close'],
                    volume=row['volume'],
                    turnover=row.get('turnover', 0),
                    timestamp=row['timestamp']
                )
                bars.append(bar)
                self.trading_data[symbol]["bars"].append(bar)
                
            # Calculate initial MAs
            closes = [bar.close_price for bar in self.trading_data[symbol]["bars"]]
            
            if len(closes) >= self.fast_window:
                if self.ma_type == "simple":
                    self.trading_data[symbol]["fast_ma"] = np.mean(closes[-self.fast_window:])
                else:  # exponential
                    self.trading_data[symbol]["fast_ma"] = self._calculate_ema(
                        closes, self.fast_window
                    )
                    
            if len(closes) >= self.slow_window:
                if self.ma_type == "simple":
                    self.trading_data[symbol]["slow_ma"] = np.mean(closes[-self.slow_window:])
                else:  # exponential
                    self.trading_data[symbol]["slow_ma"] = self._calculate_ema(
                        closes, self.slow_window
                    )
                    
            # Set previous MA values
            self.trading_data[symbol]["prev_fast_ma"] = self.trading_data[symbol]["fast_ma"]
            self.trading_data[symbol]["prev_slow_ma"] = self.trading_data[symbol]["slow_ma"]
            
            logger.info(
                f"Initialized {symbol} MAs: Fast({self.fast_window})={self.trading_data[symbol]['fast_ma']:.2f}, "
                f"Slow({self.slow_window})={self.trading_data[symbol]['slow_ma']:.2f}"
            )
            
        except Exception as e:
            logger.error(f"Error initializing MA data for {symbol}: {e}")
            
    def _calculate_ema(self, prices: List[float], window: int) -> float:
        """Calculate exponential moving average"""
        if len(prices) < window:
            return 0.0
            
        weights = np.exp(np.linspace(-1., 0., window))
        weights /= weights.sum()
        
        ema = np.average(
            prices[-window:],
            weights=weights
        )
        return ema
            
    def on_tick(self, tick: TickData):
        """Process new tick data"""
        symbol = tick.symbol
        
        if symbol not in self.trading_data:
            return
            
        # Update last price
        self.trading_data[symbol]["last_price"] = tick.last_price
        
        # Only process if we have enough data
        if not self.trading_data[symbol]["fast_ma"] or not self.trading_data[symbol]["slow_ma"]:
            return
            
        # Check if we need to update trailing stops
        self._check_risk_management(symbol, tick.last_price)
            
    def on_bar(self, bar: BarData):
        """Process new bar data and make trading decisions"""
        symbol = bar.symbol
        
        if symbol not in self.trading_data:
            return
            
        # Add bar to history
        self.trading_data[symbol]["bars"].append(bar)
        
        # Update moving averages
        self._update_moving_averages(symbol)
        
        # Check for trading signals
        self._check_trading_signals(symbol)
        
    def _update_moving_averages(self, symbol: str):
        """Update moving averages with new data"""
        bars = self.trading_data[symbol]["bars"]
        
        if len(bars) < self.fast_window:
            return
            
        # Store previous values
        self.trading_data[symbol]["prev_fast_ma"] = self.trading_data[symbol]["fast_ma"]
        self.trading_data[symbol]["prev_slow_ma"] = self.trading_data[symbol]["slow_ma"]
        
        # Calculate new values
        closes = [bar.close_price for bar in bars]
        
        if self.ma_type == "simple":
            self.trading_data[symbol]["fast_ma"] = np.mean(closes[-self.fast_window:])
            if len(closes) >= self.slow_window:
                self.trading_data[symbol]["slow_ma"] = np.mean(closes[-self.slow_window:])
        else:  # exponential
            self.trading_data[symbol]["fast_ma"] = self._calculate_ema(closes, self.fast_window)
            if len(closes) >= self.slow_window:
                self.trading_data[symbol]["slow_ma"] = self._calculate_ema(closes, self.slow_window)
                
    def _check_trading_signals(self, symbol: str):
        """Check for trading signals based on MA crossover"""
        data = self.trading_data[symbol]
        
        # Skip if not enough data yet
        if not data["prev_fast_ma"] or not data["prev_slow_ma"]:
            return
            
        # Current MA values
        fast_ma = data["fast_ma"]
        slow_ma = data["slow_ma"]
        
        # Previous MA values
        prev_fast_ma = data["prev_fast_ma"]
        prev_slow_ma = data["prev_slow_ma"]
        
        current_position = self.get_position(symbol)
        current_price = data["last_price"]
        
        # Check for crossover (bullish: fast MA crosses above slow MA)
        bullish_crossover = prev_fast_ma <= prev_slow_ma and fast_ma > slow_ma
        
        # Check for crossunder (bearish: fast MA crosses below slow MA)
        bearish_crossover = prev_fast_ma >= prev_slow_ma and fast_ma < slow_ma
        
        # Implement trading logic
        if bullish_crossover and current_position <= 0:
            # Buy signal
            price_to_buy = current_price * (1 + self.price_add_pct)  # Add small buffer
            self.buy(
                symbol=symbol,
                price=price_to_buy,
                volume=self.order_volume,
                order_type=OrderType.LO  # Limit order
            )
            
            # Record entry price for trailing stop
            data["entry_price"] = current_price
            data["highest_price_since_entry"] = current_price
            data["last_order_time"] = datetime.now()
            
            logger.info(f"BUY SIGNAL: {symbol} - Fast MA ({fast_ma:.2f}) crossed above Slow MA ({slow_ma:.2f})")
            
        elif bearish_crossover and current_position >= 0:
            # Sell signal
            price_to_sell = current_price * (1 - self.price_add_pct)  # Subtract small buffer
            self.sell(
                symbol=symbol,
                price=price_to_sell,
                volume=self.order_volume if current_position == 0 else abs(current_position),
                order_type=OrderType.LO  # Limit order
            )
            
            # Record entry price for trailing stop
            if current_position == 0:  # New short position
                data["entry_price"] = current_price
                data["lowest_price_since_entry"] = current_price
            
            data["last_order_time"] = datetime.now()
            
            logger.info(f"SELL SIGNAL: {symbol} - Fast MA ({fast_ma:.2f}) crossed below Slow MA ({slow_ma:.2f})")
            
    def _check_risk_management(self, symbol: str, current_price: float):
        """Apply risk management rules including trailing stop"""
        data = self.trading_data[symbol]
        current_position = self.get_position(symbol)
        
        # No position, no risk management needed
        if current_position == 0:
            return
            
        # Update highest/lowest price since entry
        if current_position > 0:  # Long position
            if current_price > data["highest_price_since_entry"]:
                data["highest_price_since_entry"] = current_price
                
            # Trailing stop loss (e.g., 5% from highest)
            stop_price = data["highest_price_since_entry"] * (1 - float(os.getenv("TRAILING_STOP_PCT", 5)) / 100)
            
            if current_price < stop_price:
                logger.info(
                    f"TRAILING STOP: {symbol} - Price {current_price:.2f} below stop at {stop_price:.2f} "
                    f"(max: {data['highest_price_since_entry']:.2f})"
                )
                
                self.sell(
                    symbol=symbol,
                    price=current_price * 0.99,  # Slightly below current price to ensure execution
                    volume=current_position,
                    order_type=OrderType.LO
                )
                
        elif current_position < 0:  # Short position
            if current_price < data["lowest_price_since_entry"]:
                data["lowest_price_since_entry"] = current_price
                
            # Trailing stop loss for shorts (e.g., 5% above lowest)
            stop_price = data["lowest_price_since_entry"] * (1 + float(os.getenv("TRAILING_STOP_PCT", 5)) / 100)
            
            if current_price > stop_price:
                logger.info(
                    f"TRAILING STOP: {symbol} - Price {current_price:.2f} above stop at {stop_price:.2f} "
                    f"(min: {data['lowest_price_since_entry']:.2f})"
                )
                
                self.buy(
                    symbol=symbol,
                    price=current_price * 1.01,  # Slightly above current price to ensure execution
                    volume=abs(current_position),
                    order_type=OrderType.LO
                )
                
    def generate_stats(self, symbol: str) -> Dict[str, Any]:
        """Generate strategy statistics"""
        data = self.trading_data[symbol]
        return {
            "symbol": symbol,
            "position": self.get_position(symbol),
            "fast_ma": data["fast_ma"],
            "slow_ma": data["slow_ma"],
            "entry_price": data["entry_price"],
            "last_price": data["last_price"],
            "profit_pct": ((data["last_price"] / data["entry_price"]) - 1) * 100 
                          if data["entry_price"] > 0 else 0.0,
            "bars_count": len(data["bars"])
        } 