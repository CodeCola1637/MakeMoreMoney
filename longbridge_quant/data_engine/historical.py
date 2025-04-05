"""
Historical data fetching and processing module
"""
import os
import logging
from typing import Dict, List, Optional, Union, Tuple
from datetime import datetime, date, timedelta
import pandas as pd
import numpy as np

from longport.openapi import Period, AdjustType

from ..api_client.client import LongPortClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("historical_data")

class HistoricalDataLoader:
    """
    Loads and processes historical market data from LongPort API
    """
    
    def __init__(self, client: LongPortClient):
        self.client = client
        self.cache: Dict[str, pd.DataFrame] = {}  # Simple cache for performance
        
    def get_bars(
        self,
        symbol: str,
        period: Period = Period.Day,
        start_date: Optional[Union[date, datetime, str]] = None,
        end_date: Optional[Union[date, datetime, str]] = None,
        count: int = 100,
        adjust_type: AdjustType = AdjustType.NoAdjust,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        Get historical bars for a symbol with flexible date options
        """
        # Generate cache key
        cache_key = f"{symbol}_{str(period)}_{start_date}_{end_date}_{count}_{str(adjust_type)}"
        
        # Return from cache if available
        if use_cache and cache_key in self.cache:
            logger.debug(f"Using cached data for {symbol}")
            return self.cache[cache_key].copy()
            
        # Parse dates if needed
        parsed_start, parsed_end = self._parse_dates(start_date, end_date)
        
        # Fetch data from API
        try:
            if parsed_start and parsed_end:
                candlesticks = self.client.get_history_candlesticks(
                    symbol=symbol,
                    period=period,
                    adjust_type=adjust_type,
                    start=parsed_start,
                    end=parsed_end
                )
            else:
                candlesticks = self.client.get_history_candlesticks(
                    symbol=symbol,
                    period=period,
                    adjust_type=adjust_type,
                    count=count
                )
                
            # Convert to DataFrame
            if not candlesticks:
                logger.warning(f"No data returned for {symbol}")
                return pd.DataFrame()
                
            data = []
            for candle in candlesticks:
                data.append({
                    'symbol': symbol,
                    'timestamp': candle.timestamp,
                    'open': candle.open,
                    'high': candle.high,
                    'low': candle.low,
                    'close': candle.close,
                    'volume': candle.volume,
                    'turnover': candle.turnover
                })
                
            df = pd.DataFrame(data)
            
            # Cache the result
            if use_cache:
                self.cache[cache_key] = df.copy()
                
            return df
            
        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return pd.DataFrame()
            
    def _parse_dates(
        self, 
        start_date: Optional[Union[date, datetime, str]], 
        end_date: Optional[Union[date, datetime, str]]
    ) -> Tuple[Optional[date], Optional[date]]:
        """Parse and validate date inputs"""
        parsed_start, parsed_end = None, None
        
        if start_date:
            if isinstance(start_date, str):
                try:
                    parsed_start = pd.to_datetime(start_date).date()
                except:
                    logger.error(f"Invalid start date format: {start_date}")
            elif isinstance(start_date, datetime):
                parsed_start = start_date.date()
            else:
                parsed_start = start_date
                
        if end_date:
            if isinstance(end_date, str):
                try:
                    parsed_end = pd.to_datetime(end_date).date()
                except:
                    logger.error(f"Invalid end date format: {end_date}")
            elif isinstance(end_date, datetime):
                parsed_end = end_date.date()
            else:
                parsed_end = end_date
                
        # Validate date range
        if parsed_start and parsed_end and parsed_start > parsed_end:
            logger.warning("Start date is after end date, swapping")
            parsed_start, parsed_end = parsed_end, parsed_start
            
        return parsed_start, parsed_end
        
    def clear_cache(self):
        """Clear the data cache"""
        self.cache = {}
        logger.info("Historical data cache cleared")
        
    def get_multiple_symbols(
        self,
        symbols: List[str],
        period: Period = Period.Day,
        start_date: Optional[Union[date, datetime, str]] = None,
        end_date: Optional[Union[date, datetime, str]] = None,
        count: int = 100,
        adjust_type: AdjustType = AdjustType.NoAdjust
    ) -> Dict[str, pd.DataFrame]:
        """
        Get historical data for multiple symbols
        """
        result = {}
        for symbol in symbols:
            df = self.get_bars(
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                count=count,
                adjust_type=adjust_type
            )
            result[symbol] = df
            
        return result
        
    def save_to_csv(self, symbol: str, filepath: str, **kwargs):
        """
        Save historical data to CSV file
        """
        if symbol not in self.cache:
            logger.warning(f"No cached data for {symbol}")
            return False
            
        try:
            self.cache[symbol].to_csv(filepath, index=False)
            logger.info(f"Saved data for {symbol} to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error saving data to {filepath}: {e}")
            return False 