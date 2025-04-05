"""
Strategy template base class inspired by vn.py CTA module
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Union
from datetime import datetime
import pandas as pd
import numpy as np
from decimal import Decimal

from longport.openapi import OrderSide, OrderType, TimeInForceType, PushQuote

from ..api_client.client import LongPortClient
from ..data_engine.realtime import QuoteProcessor

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("strategy_template")

class StrategyParam:
    """Base class for strategy parameters with validation"""
    def __init__(self, name: str, value: Any, min_value: Any = None, max_value: Any = None):
        self.name = name
        self.value = value
        self.min_value = min_value
        self.max_value = max_value
        
    def validate(self) -> bool:
        """Validate parameter is within range"""
        if self.min_value is not None and self.value < self.min_value:
            return False
        if self.max_value is not None and self.value > self.max_value:
            return False
        return True
        
    def __str__(self):
        return f"{self.name}={self.value}"


class TickData:
    """Tick data structure similar to vn.py format"""
    def __init__(self, symbol: str, quote: PushQuote):
        self.symbol = symbol
        self.last_price = quote.last_done
        self.volume = quote.volume
        self.turnover = quote.turnover
        self.open_price = quote.open
        self.high_price = quote.high
        self.low_price = quote.low
        self.pre_close = quote.prev_close
        self.timestamp = datetime.now()
        
    def __str__(self):
        return f"Tick({self.symbol}, {self.last_price})"


class BarData:
    """Bar data structure similar to vn.py format"""
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
    def from_quote(cls, symbol: str, quote: PushQuote):
        """Create bar from quote"""
        return cls(
            symbol=symbol,
            open_price=quote.open,
            high_price=quote.high,
            low_price=quote.low,
            close_price=quote.last_done,
            volume=quote.volume,
            turnover=quote.turnover
        )
        
    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """Create bar from dictionary"""
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
        
    def __str__(self):
        return f"Bar({self.symbol}, {self.close_price}, {self.timestamp})"


class CtaTemplate(ABC):
    """
    Base strategy template resembling vn.py CtaTemplate
    """
    parameters = {}  # Should be overridden by subclasses
    
    def __init__(
        self,
        client: LongPortClient,
        quote_processor: QuoteProcessor,
        symbols: List[str] = None
    ):
        self.client = client
        self.quote_processor = quote_processor
        self.symbols = symbols or []
        
        # Trading variables
        self.pos: Dict[str, int] = {symbol: 0 for symbol in self.symbols}
        self.active = False
        
        # Apply default parameters
        for name, value in self.parameters.items():
            setattr(self, name, value)
        
        # Register callbacks
        for symbol in self.symbols:
            self.quote_processor.register_price_callback(
                lambda s, q: self._on_quote_callback(s, q)
            )
            
    def _on_quote_callback(self, symbol: str, quote: PushQuote):
        """
        Internal callback for quotes that creates tick and calls the strategy
        """
        if not self.active:
            return
            
        if symbol in self.symbols:
            tick = TickData(symbol, quote)
            self.on_tick(tick)
            
    def start(self):
        """Start the strategy"""
        if not self.active:
            logger.info(f"Starting strategy for symbols: {self.symbols}")
            self.active = True
            self.on_start()
            
    def stop(self):
        """Stop the strategy"""
        if self.active:
            logger.info("Stopping strategy")
            self.active = False
            self.on_stop()
            
    def buy(self, symbol: str, price: float, volume: int, order_type: OrderType = OrderType.LO):
        """Send buy order"""
        if not self.active:
            logger.warning("Cannot send order - strategy not active")
            return None
            
        logger.info(f"BUY {symbol}: {volume} @ {price}")
        
        decimal_price = Decimal(str(price))
        result = self.client.create_order(
            symbol=symbol,
            order_type=order_type,
            side=OrderSide.Buy,
            quantity=volume,
            time_in_force=TimeInForceType.Day,
            submitted_price=decimal_price if order_type == OrderType.LO else None,
            remark="Strategy order"
        )
        
        # Update position tracking (optimistically)
        self.pos[symbol] = self.pos.get(symbol, 0) + volume
        
        return result
        
    def sell(self, symbol: str, price: float, volume: int, order_type: OrderType = OrderType.LO):
        """Send sell order"""
        if not self.active:
            logger.warning("Cannot send order - strategy not active")
            return None
            
        logger.info(f"SELL {symbol}: {volume} @ {price}")
        
        decimal_price = Decimal(str(price))
        result = self.client.create_order(
            symbol=symbol,
            order_type=order_type,
            side=OrderSide.Sell,
            quantity=volume,
            time_in_force=TimeInForceType.Day,
            submitted_price=decimal_price if order_type == OrderType.LO else None,
            remark="Strategy order"
        )
        
        # Update position tracking (optimistically)
        self.pos[symbol] = self.pos.get(symbol, 0) - volume
        
        return result
    
    def get_position(self, symbol: str) -> int:
        """Get current position for symbol"""
        return self.pos.get(symbol, 0)
        
    def update_parameters(self, params: Dict[str, Any]):
        """Update strategy parameters"""
        for name, value in params.items():
            if name in self.parameters:
                logger.info(f"Updating parameter {name}: {getattr(self, name)} -> {value}")
                setattr(self, name, value)
                
    @abstractmethod
    def on_tick(self, tick: TickData):
        """
        Called when new market data arrives
        Must be implemented by subclasses
        """
        pass
        
    def on_bar(self, bar: BarData):
        """Called when a new bar is formed"""
        pass
        
    def on_start(self):
        """Called when strategy is started"""
        pass
        
    def on_stop(self):
        """Called when strategy is stopped"""
        pass
        
    def __str__(self):
        """String representation of strategy"""
        params = ", ".join(f"{k}={getattr(self, k)}" for k in self.parameters.keys())
        return f"{self.__class__.__name__}({params})" 