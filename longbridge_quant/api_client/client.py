"""
LongPort API client wrapper with automatic retry mechanisms
"""
import os
import time
import functools
from typing import List, Dict, Any, Callable, Optional, Union
from decimal import Decimal
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

from longport.openapi import (
    Config, 
    TradeContext, 
    QuoteContext, 
    OrderSide, 
    OrderType, 
    TimeInForceType,
    Period,
    AdjustType,
    SubType,
    PushQuote,
    TradeSession,
    Market,
    OrderStatus,
    OrderTag,
    TopicType,
    WatchlistGroup,
    CalcIndex,
    TradingSessionInfo
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("longport_client")

# Load environment variables
load_dotenv()

def retry(max_retries=3, delay=1.0, backoff=2.0, exceptions=(Exception,)):
    """
    Retry decorator with exponential backoff for API calls
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retry_count = 0
            current_delay = delay
            
            while retry_count < max_retries:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    retry_count += 1
                    if retry_count >= max_retries:
                        logger.error(f"Max retries reached for {func.__name__}: {e}")
                        raise
                        
                    logger.warning(
                        f"Retrying {func.__name__} after error: {e}. "
                        f"Retry {retry_count}/{max_retries} in {current_delay:.2f}s"
                    )
                    time.sleep(current_delay)
                    current_delay *= backoff
                    
        return wrapper
    return decorator


class LongPortClient:
    """
    Unified client for LongPort API access with retry capabilities
    """

    def __init__(self, use_websocket=True):
        """
        Initialize both trade and quote contexts
        
        Args:
            use_websocket (bool): Whether to use WebSocket for real-time data
        """
        # 不再使用API_BASE_URL和API_WS_URL环境变量
        # 让SDK使用默认URL设置，这些URL会自动解析到hosts文件中配置的IP地址
        
        # 检查SSL验证设置
        self.disable_ssl_verify = os.getenv("LONGPORT_DISABLE_SSL_VERIFY", "false").lower() == "true"
        if self.disable_ssl_verify:
            logger.warning("SSL verification is disabled. This is not recommended for production.")
            
        # 创建配置，使用默认URL设置
        self.config = Config(
            app_key=os.getenv("LONG_PORT_APP_KEY"),
            app_secret=os.getenv("LONG_PORT_APP_SECRET"),
            access_token=os.getenv("LONG_PORT_ACCESS_TOKEN")
        )
        
        # Initialize contexts
        self._trade_ctx = None
        self._quote_ctx = None
        
        # Quote subscription callbacks
        self._quote_callbacks = {}
        self._depth_callbacks = {}
        self._broker_callbacks = {}
        self._trade_callbacks = {}
        
        # Reconnection handling
        self._use_websocket = use_websocket
        self._last_connected = None
        self._reconnect_interval = 60  # seconds
        
    def __del__(self):
        """Clean up resources"""
        self.close()
        
    def close(self):
        """Close connections and clean up resources"""
        if self._trade_ctx:
            try:
                self._trade_ctx.close()
            except Exception as e:
                logger.warning(f"Error closing trade context: {e}")
            self._trade_ctx = None
            
        if self._quote_ctx:
            try:
                self._quote_ctx.close()
            except Exception as e:
                logger.warning(f"Error closing quote context: {e}")
            self._quote_ctx = None
    
    def _check_connection(self):
        """Check and reconnect if necessary"""
        now = datetime.now()
        if (self._last_connected is None or 
            (now - self._last_connected).total_seconds() > self._reconnect_interval):
            logger.info("Checking connection status...")
            self.close()
            # Accessing properties will trigger reconnection
            _ = self.trade_ctx
            _ = self.quote_ctx
            self._last_connected = now
            
    @property
    def trade_ctx(self) -> TradeContext:
        """Lazy load trade context"""
        if self._trade_ctx is None:
            logger.info("Initializing trade context...")
            self._trade_ctx = TradeContext(self.config)
            self._last_connected = datetime.now()
        return self._trade_ctx
    
    @property
    def quote_ctx(self) -> QuoteContext:
        """Lazy load quote context"""
        if self._quote_ctx is None:
            logger.info("Initializing quote context...")
            self._quote_ctx = QuoteContext(self.config)
            self._setup_quote_callbacks()
            self._last_connected = datetime.now()
        return self._quote_ctx
    
    def _setup_quote_callbacks(self):
        """Set up callbacks for quote subscription"""
        # 不同版本的SDK可能有不同的回调设置方法
        try:
            # Quote update callback
            def on_quote(symbol: str, event: PushQuote):
                if symbol in self._quote_callbacks:
                    for callback in self._quote_callbacks.get(symbol, []):
                        try:
                            callback(symbol, event)
                        except Exception as e:
                            logger.error(f"Error in quote callback for {symbol}: {e}")
            
            # 只设置支持的回调
            if hasattr(self.quote_ctx, 'set_on_quote'):
                self.quote_ctx.set_on_quote(on_quote)
                
            # 其他回调如果存在，则设置
            if hasattr(self.quote_ctx, 'set_on_depth'):
                def on_depth(symbol: str, event):
                    if symbol in self._depth_callbacks:
                        for callback in self._depth_callbacks.get(symbol, []):
                            try:
                                callback(symbol, event)
                            except Exception as e:
                                logger.error(f"Error in depth callback for {symbol}: {e}")
                self.quote_ctx.set_on_depth(on_depth)
                
            if hasattr(self.quote_ctx, 'set_on_brokers'):
                def on_brokers(symbol: str, event):
                    if symbol in self._broker_callbacks:
                        for callback in self._broker_callbacks.get(symbol, []):
                            try:
                                callback(symbol, event)
                            except Exception as e:
                                logger.error(f"Error in broker callback for {symbol}: {e}")
                self.quote_ctx.set_on_brokers(on_brokers)
                
            if hasattr(self.quote_ctx, 'set_on_trade'):
                def on_trade(symbol: str, event):
                    if symbol in self._trade_callbacks:
                        for callback in self._trade_callbacks.get(symbol, []):
                            try:
                                callback(symbol, event)
                            except Exception as e:
                                logger.error(f"Error in trade callback for {symbol}: {e}")
                self.quote_ctx.set_on_trade(on_trade)
                
        except Exception as e:
            logger.warning(f"Error setting up quote callbacks: {e}")
    
    def register_quote_callback(self, symbol: str, callback: Callable):
        """Register callback for quote updates"""
        if symbol not in self._quote_callbacks:
            self._quote_callbacks[symbol] = []
        self._quote_callbacks[symbol].append(callback)
        
    def register_depth_callback(self, symbol: str, callback: Callable):
        """Register callback for depth updates"""
        if symbol not in self._depth_callbacks:
            self._depth_callbacks[symbol] = []
        self._depth_callbacks[symbol].append(callback)
        
    def register_broker_callback(self, symbol: str, callback: Callable):
        """Register callback for broker updates"""
        if symbol not in self._broker_callbacks:
            self._broker_callbacks[symbol] = []
        self._broker_callbacks[symbol].append(callback)
        
    def register_trade_callback(self, symbol: str, callback: Callable):
        """Register callback for trade updates"""
        if symbol not in self._trade_callbacks:
            self._trade_callbacks[symbol] = []
        self._trade_callbacks[symbol].append(callback)
        
    # ===== QUOTE API METHODS =====
    
    @retry(max_retries=3, delay=0.3)
    def get_market_trading_days(self, market: Market, begin_date, end_date):
        """Get trading days for a market in a date range"""
        self._check_connection()
        try:
            return self.quote_ctx.trading_days(
                market=market,
                begin_date=begin_date,
                end_date=end_date
            )
        except Exception as e:
            logger.error(f"Error getting trading days for {market}: {e}")
            raise
    
    @retry(max_retries=3, delay=0.3)
    def get_trading_sessions(self):
        """Get trading sessions for all markets"""
        self._check_connection()
        try:
            return self.quote_ctx.trading_session()
        except Exception as e:
            logger.error(f"Error getting trading sessions: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_stock_info(self, symbols: List[str]):
        """Get basic information for stocks"""
        self._check_connection()
        try:
            return self.quote_ctx.static_info(symbols=symbols)
        except Exception as e:
            logger.error(f"Error getting stock info for {symbols}: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_quote(self, symbols: List[str]):
        """Get real-time quotes for stocks"""
        self._check_connection()
        try:
            return self.quote_ctx.quote(symbols=symbols)
        except Exception as e:
            logger.error(f"Error getting quotes for {symbols}: {e}")
            raise
    
    @retry(max_retries=3, delay=0.3)
    def get_option_quote(self, symbols: List[str]):
        """Get option quotes"""
        self._check_connection()
        try:
            return self.quote_ctx.option_quote(symbols=symbols)
        except Exception as e:
            logger.error(f"Error getting option quotes for {symbols}: {e}")
            raise
    
    @retry(max_retries=3, delay=0.3)
    def get_warrant_quote(self, symbols: List[str]):
        """Get warrant quotes"""
        self._check_connection()
        try:
            return self.quote_ctx.warrant_quote(symbols=symbols)
        except Exception as e:
            logger.error(f"Error getting warrant quotes for {symbols}: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_depth(self, symbol: str):
        """Get order book depth"""
        self._check_connection()
        try:
            return self.quote_ctx.depth(symbol=symbol)
        except Exception as e:
            logger.error(f"Error getting depth for {symbol}: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_brokers(self, symbol: str):
        """Get broker queue"""
        self._check_connection()
        try:
            return self.quote_ctx.brokers(symbol=symbol)
        except Exception as e:
            logger.error(f"Error getting brokers for {symbol}: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_trade_ticks(self, symbol: str, count: int = 10):
        """Get recent trades"""
        self._check_connection()
        try:
            return self.quote_ctx.trades(symbol=symbol, count=count)
        except Exception as e:
            logger.error(f"Error getting trades for {symbol}: {e}")
            raise
    
    @retry(max_retries=3, delay=0.3)
    def get_candlesticks(self, symbol: str, period: Period = Period.Day, count: int = 100, adjust_type: AdjustType = AdjustType.NoAdjust):
        """Get candlestick data"""
        self._check_connection()
        try:
            return self.quote_ctx.candlesticks(symbol=symbol, period=period, count=count, adjust_type=adjust_type)
        except Exception as e:
            logger.error(f"Error getting candlesticks for {symbol}: {e}")
            raise
    
    @retry(max_retries=3, delay=0.3)
    def get_history_candlesticks(
        self,
        symbol: str,
        period: Period = Period.Day,
        adjust_type: AdjustType = AdjustType.NoAdjust,
        start=None,
        end=None,
        count: int = 100
    ):
        """
        Get historical candlestick data with flexible date or count options
        """
        self._check_connection()
        try:
            if start and end:
                # Get data by date range
                return self.quote_ctx.history_candlesticks_by_date(
                    symbol=symbol,
                    period=period,
                    adjust_type=adjust_type,
                    start=start,
                    end=end
                )
            else:
                # Get data by count
                return self.quote_ctx.history_candlesticks_by_offset(
                    symbol=symbol,
                    period=period,
                    adjust_type=adjust_type,
                    forward=False,
                    count=count,
                    end_timestamp=None  # Use the latest
                )
        except Exception as e:
            logger.error(f"Error fetching history data for {symbol}: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_calc_indexes(self, symbols: List[str], indexes: List[CalcIndex]):
        """Get calculated indexes for stocks"""
        self._check_connection()
        try:
            return self.quote_ctx.calc_indexes(symbols=symbols, indexes=indexes)
        except Exception as e:
            logger.error(f"Error getting calc indexes for {symbols}: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def subscribe_quotes(self, symbols: List[str], sub_types: List[SubType] = None, is_first_push: bool = True):
        """Subscribe to real-time quotes"""
        self._check_connection()
        if sub_types is None:
            sub_types = [SubType.Quote]
            
        try:
            result = self.quote_ctx.subscribe(
                symbols=symbols,
                sub_types=sub_types,
                is_first_push=is_first_push
            )
            logger.info(f"Subscribed to quotes for: {symbols}")
            return result
        except Exception as e:
            logger.error(f"Error subscribing to quotes: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def unsubscribe_quotes(self, symbols: List[str], sub_types: List[SubType] = None):
        """Unsubscribe from real-time quotes"""
        self._check_connection()
        if sub_types is None:
            sub_types = [SubType.Quote]
            
        try:
            result = self.quote_ctx.unsubscribe(
                symbols=symbols,
                sub_types=sub_types
            )
            logger.info(f"Unsubscribed from quotes for: {symbols}")
            return result
        except Exception as e:
            logger.error(f"Error unsubscribing from quotes: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_subscription(self):
        """Get current subscriptions"""
        self._check_connection()
        try:
            return self.quote_ctx.subscription()
        except Exception as e:
            logger.error(f"Error getting subscriptions: {e}")
            raise
            
    # ===== TRADE API METHODS =====
    
    @retry(max_retries=3, delay=0.5)
    def create_order(
        self,
        symbol: str,
        order_type: OrderType,
        side: OrderSide,
        quantity: Union[int, Decimal],
        time_in_force: TimeInForceType,
        submitted_price: Optional[Decimal] = None,
        outside_rth: bool = False,
        remark: str = ""
    ) -> Dict[str, Any]:
        """
        Create an order with retry mechanism
        """
        self._check_connection()
        
        # 安全获取 side 名称
        side_name = getattr(side, 'name', str(side))
        
        logger.info(
            f"Creating order: {symbol} {side_name} {quantity} @ "
            f"{submitted_price if submitted_price else 'Market'}"
        )
        
        if isinstance(quantity, int):
            quantity = Decimal(quantity)
        
        try:
            # 更安全的方式处理 outside_rth
            order_params = {
                "symbol": symbol,
                "order_type": order_type,
                "side": side,
                "submitted_quantity": quantity, 
                "time_in_force": time_in_force,
                "remark": remark,
            }
            
            # 如果有价格，添加价格参数
            if submitted_price is not None:
                order_params["submitted_price"] = submitted_price
                
            # 提交订单
            result = self.trade_ctx.submit_order(**order_params)
            logger.info(f"Order created successfully: {result.order_id}")
            return result
        except Exception as e:
            logger.error(f"Error creating order: {e}")
            raise
    
    @retry(max_retries=3, delay=0.3)
    def get_account_balance(self):
        """Get account cash balance information"""
        self._check_connection()
        try:
            return self.trade_ctx.account_balance()
        except Exception as e:
            logger.error(f"Error getting account balance: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_positions(self):
        """Get current positions"""
        self._check_connection()
        try:
            return self.trade_ctx.stock_positions()
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_today_orders(self, symbol: str = None, status: List[OrderStatus] = None):
        """
        Get today's orders
        
        Args:
            symbol: Optional filter by symbol
            status: Optional filter by order status
        """
        self._check_connection()
        try:
            return self.trade_ctx.today_orders(symbol=symbol, status=status)
        except Exception as e:
            logger.error(f"Error getting today's orders: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_history_orders(
        self, 
        start_at: datetime = None, 
        end_at: datetime = None,
        symbol: str = None,
        status: List[OrderStatus] = None,
        side: OrderSide = None,
        market: Market = None
    ):
        """
        Get historical orders with filters
        
        Args:
            start_at: Start time
            end_at: End time
            symbol: Filter by symbol
            status: Filter by order status
            side: Filter by order side
            market: Filter by market
        """
        self._check_connection()
        if start_at is None:
            start_at = datetime.now() - timedelta(days=7)
        if end_at is None:
            end_at = datetime.now()
            
        try:
            return self.trade_ctx.history_orders(
                start_at=start_at, 
                end_at=end_at,
                symbol=symbol,
                status=status,
                side=side,
                market=market
            )
        except Exception as e:
            logger.error(f"Error getting history orders: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def cancel_order(self, order_id: str):
        """Cancel an order by order ID"""
        self._check_connection()
        try:
            result = self.trade_ctx.cancel_order(order_id=order_id)
            logger.info(f"Cancelled order: {order_id}")
            return result
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def batch_cancel_orders(self, order_ids: List[str]):
        """Cancel multiple orders"""
        self._check_connection()
        try:
            results = []
            for order_id in order_ids:
                try:
                    result = self.trade_ctx.cancel_order(order_id=order_id)
                    logger.info(f"Cancelled order: {order_id}")
                    results.append({"order_id": order_id, "success": True, "result": result})
                except Exception as e:
                    logger.error(f"Error cancelling order {order_id}: {e}")
                    results.append({"order_id": order_id, "success": False, "error": str(e)})
            return results
        except Exception as e:
            logger.error(f"Error in batch cancel orders: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_order_detail(self, order_id: str):
        """Get detailed information about an order"""
        self._check_connection()
        try:
            return self.trade_ctx.order_detail(order_id=order_id)
        except Exception as e:
            logger.error(f"Error getting order detail for {order_id}: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_today_executions(self, symbol: str = None):
        """Get today's executions/trades"""
        self._check_connection()
        try:
            return self.trade_ctx.today_executions(symbol=symbol)
        except Exception as e:
            logger.error(f"Error getting today's executions: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_history_executions(
        self,
        start_at: datetime = None,
        end_at: datetime = None,
        symbol: str = None
    ):
        """Get historical executions/trades"""
        self._check_connection()
        if start_at is None:
            start_at = datetime.now() - timedelta(days=7)
        if end_at is None:
            end_at = datetime.now()
            
        try:
            return self.trade_ctx.history_executions(
                start_at=start_at,
                end_at=end_at,
                symbol=symbol
            )
        except Exception as e:
            logger.error(f"Error getting history executions: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_dividends(
        self,
        start_at: datetime = None,
        end_at: datetime = None,
        symbol: str = None,
        market: Market = None
    ):
        """Get dividend information"""
        self._check_connection()
        if start_at is None:
            start_at = datetime.now() - timedelta(days=365)
        if end_at is None:
            end_at = datetime.now()
            
        try:
            return self.trade_ctx.dividends(
                start_at=start_at,
                end_at=end_at,
                symbol=symbol,
                market=market
            )
        except Exception as e:
            logger.error(f"Error getting dividends: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_stock_positions(self, symbol: str = None):
        """Get stock positions"""
        self._check_connection()
        try:
            return self.trade_ctx.stock_positions(symbol=symbol)
        except Exception as e:
            logger.error(f"Error getting stock positions: {e}")
            raise
            
    @retry(max_retries=3, delay=0.3)
    def get_margin_ratio(self, symbol: str):
        """Get margin ratio for a stock"""
        self._check_connection()
        try:
            return self.trade_ctx.margin_ratio(symbol=symbol)
        except Exception as e:
            logger.error(f"Error getting margin ratio for {symbol}: {e}")
            raise 