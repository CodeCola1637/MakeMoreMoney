"""
Real-time market data processing and event handling
"""
import time
import threading
from typing import Dict, List, Callable, Any, Optional
import logging
import queue
from datetime import datetime
import pandas as pd
import numpy as np

from longport.openapi import PushQuote, SubType, Candlestick

from ..api_client.client import LongPortClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("realtime_data")

class QuoteProcessor:
    """
    Real-time quote processor that maintains latest market data
    and triggers event-based actions
    """
    
    def __init__(self, client: LongPortClient):
        self.client = client
        self.latest_quotes: Dict[str, PushQuote] = {}
        self.latest_candlesticks: Dict[str, Dict[str, List[Candlestick]]] = {}
        self.price_queue = queue.Queue(maxsize=10000)  # Buffer for high-frequency data
        self.running = False
        self.processing_thread = None
        
        # Event callbacks
        self.price_update_callbacks: List[Callable] = []
        self.candlestick_update_callbacks: List[Callable] = []
    
    def start(self):
        """Start the quote processor"""
        if self.running:
            return
            
        self.running = True
        self.processing_thread = threading.Thread(
            target=self._process_queue,
            daemon=True
        )
        self.processing_thread.start()
        logger.info("Quote processor started")
        
    def stop(self):
        """Stop the quote processor"""
        self.running = False
        if self.processing_thread:
            self.processing_thread.join(timeout=5.0)
        logger.info("Quote processor stopped")
        
    def subscribe(self, symbols: List[str], sub_types: Optional[List[SubType]] = None):
        """
        Subscribe to market data for symbols
        """
        if sub_types is None:
            sub_types = [SubType.Quote, SubType.Depth, SubType.Brokers]
            
        # Register callbacks for each symbol
        for symbol in symbols:
            self.client.register_quote_callback(symbol, self._on_quote_update)
            
        # Subscribe via client
        return self.client.subscribe_quotes(symbols, sub_types)
        
    def _on_quote_update(self, symbol: str, quote: PushQuote):
        """Callback when new quote arrives"""
        # Update latest quote
        self.latest_quotes[symbol] = quote
        
        try:
            # Put into queue for async processing
            self.price_queue.put((symbol, quote, time.time()), block=False)
        except queue.Full:
            logger.warning("Quote processing queue is full, dropping data")
            
    def _process_queue(self):
        """Background thread to process the queue"""
        while self.running:
            try:
                symbol, quote, timestamp = self.price_queue.get(timeout=0.1)
                
                # Process and notify all callbacks
                for callback in self.price_update_callbacks:
                    try:
                        callback(symbol, quote)
                    except Exception as e:
                        logger.error(f"Error in price update callback: {e}")
                
                # Mark task as done
                self.price_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error processing quote queue: {e}")
                time.sleep(0.1)  # Prevent tight loop on error
                
    def register_price_callback(self, callback: Callable):
        """Register a callback for price updates"""
        self.price_update_callbacks.append(callback)
        
    def register_candlestick_callback(self, callback: Callable):
        """Register a callback for candlestick updates"""
        self.candlestick_update_callbacks.append(callback)
        
    def get_latest_price(self, symbol: str) -> float:
        """Get the latest price for a symbol"""
        if symbol in self.latest_quotes:
            return self.latest_quotes[symbol].last_done
        return None
        
    def get_latest_quotes(self, symbols: List[str] = None) -> Dict[str, Any]:
        """Get the latest quotes for symbols or all if None"""
        if symbols is None:
            return self.latest_quotes
            
        return {s: q for s, q in self.latest_quotes.items() if s in symbols}


class TimeBarAggregator:
    """
    Aggregates tick data into time bars (e.g., 1-minute, 5-minute bars)
    """
    
    def __init__(self, processor: QuoteProcessor, interval_seconds: int = 60):
        self.processor = processor
        self.interval_seconds = interval_seconds
        self.bars: Dict[str, pd.DataFrame] = {}
        self.current_bars: Dict[str, Dict] = {}
        self.last_bar_time: Dict[str, datetime] = {}
        
        # Register for updates
        self.processor.register_price_callback(self._on_price_update)
        
    def _on_price_update(self, symbol: str, quote: PushQuote):
        """Process a price update and aggregate into bars"""
        current_time = datetime.now()
        
        # Initialize if it's a new symbol
        if symbol not in self.current_bars:
            self.current_bars[symbol] = {
                'open': quote.last_done,
                'high': quote.last_done,
                'low': quote.last_done,
                'close': quote.last_done,
                'volume': quote.volume,
                'amount': quote.turnover,
                'start_time': current_time
            }
            self.last_bar_time[symbol] = current_time
            return
            
        # Update current bar
        self.current_bars[symbol]['high'] = max(
            self.current_bars[symbol]['high'], 
            quote.last_done
        )
        self.current_bars[symbol]['low'] = min(
            self.current_bars[symbol]['low'], 
            quote.last_done
        )
        self.current_bars[symbol]['close'] = quote.last_done
        self.current_bars[symbol]['volume'] = quote.volume 
        self.current_bars[symbol]['amount'] = quote.turnover
        
        # Check if it's time to close the bar
        elapsed = (current_time - self.last_bar_time[symbol]).total_seconds()
        if elapsed >= self.interval_seconds:
            self._close_bar(symbol, current_time)
            
    def _close_bar(self, symbol: str, current_time: datetime):
        """Close the current bar and start a new one"""
        # Store the completed bar
        bar_data = self.current_bars[symbol].copy()
        bar_data['symbol'] = symbol
        bar_data['timestamp'] = self.last_bar_time[symbol]
        
        # Add to dataframe
        if symbol not in self.bars:
            self.bars[symbol] = pd.DataFrame([bar_data])
        else:
            self.bars[symbol] = pd.concat([
                self.bars[symbol], 
                pd.DataFrame([bar_data])
            ]).reset_index(drop=True)
            
        # Start a new bar with current price
        last_price = bar_data['close']
        self.current_bars[symbol] = {
            'open': last_price,
            'high': last_price,
            'low': last_price,
            'close': last_price,
            'volume': 0,
            'amount': 0,
            'start_time': current_time
        }
        self.last_bar_time[symbol] = current_time
        
    def get_bars(self, symbol: str, count: int = 0) -> pd.DataFrame:
        """Get historical bars for a symbol"""
        if symbol not in self.bars:
            return pd.DataFrame()
            
        if count <= 0 or count >= len(self.bars[symbol]):
            return self.bars[symbol].copy()
            
        return self.bars[symbol].tail(count).copy()
        
    def reset(self):
        """Clear all bar data"""
        self.bars = {}
        self.current_bars = {}
        self.last_bar_time = {} 