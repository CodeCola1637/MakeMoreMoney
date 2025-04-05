#!/usr/bin/env python
"""
LongBridge Quant - Quantitative Trading System using LongPort API
"""
import os
import logging
import argparse
import time
from datetime import datetime, timedelta, date
import pandas as pd
from dotenv import load_dotenv
import matplotlib.pyplot as plt
from decimal import Decimal

from longport.openapi import OrderSide, OrderType, TimeInForceType, Period, AdjustType, Config, SubType

from longbridge_quant.api_client.client import LongPortClient
from longbridge_quant.data_engine.realtime import QuoteProcessor, TimeBarAggregator
from longbridge_quant.data_engine.historical import HistoricalDataLoader
from longbridge_quant.strategy.dual_ma import DualMAStrategy
from longbridge_quant.risk_management.risk_manager import RiskManager
from longbridge_quant.execution.algo_execution import AlgoExecutionManager
from longbridge_quant.utils.backtest import BacktestEngine, run_dual_ma_backtest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("main")

def load_environment():
    """Load environment variables and verify required ones are present"""
    load_dotenv()
    required_vars = [
        "LONG_PORT_APP_KEY", 
        "LONG_PORT_APP_SECRET", 
        "LONG_PORT_ACCESS_TOKEN"
    ]
    
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {missing}")
        
    return True

def run_backtest(args):
    """Run backtest mode"""
    logger.info("Starting backtest mode")
    
    # Default symbols if not specified
    symbols = args.symbols or ['AAPL.US', 'TSLA.US']
    
    # Create client
    client = LongPortClient()
    historical_loader = HistoricalDataLoader(client)
    
    # Load historical data for symbols
    data_source = {}
    for symbol in symbols:
        # Get historical data for backtesting (1 year of daily data)
        logger.info(f"Loading historical data for {symbol}")
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=365)
        
        data = historical_loader.get_bars(
            symbol=symbol,
            period=Period.Day,
            start_date=start_date,
            end_date=end_date,
            adjust_type=AdjustType.NoAdjust
        )
        
        if not data.empty:
            data_source[symbol] = data
            logger.info(f"Loaded {len(data)} bars for {symbol}")
        else:
            logger.error(f"No data available for {symbol}")
            
    if not data_source:
        logger.error("No data loaded for backtest")
        return
        
    # Run the backtest
    results = run_dual_ma_backtest(
        symbols=symbols,
        data_source=data_source,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        fast_window=args.fast_window,
        slow_window=args.slow_window,
        initial_capital=args.capital,
        plot_results=True
    )
    
    # Display results
    logger.info("Backtest results:")
    for key, value in results.items():
        logger.info(f"{key}: {value}")

def run_live_trading(args):
    """Run live trading mode"""
    logger.info("Starting live trading mode")
    
    # Default symbols if not specified
    symbols = args.symbols or ['AAPL.US', 'TSLA.US']
    
    # Create API clients
    client = LongPortClient()
    quote_processor = QuoteProcessor(client)
    
    # Initialize risk manager
    risk_manager = RiskManager(client)
    risk_manager.initialize()
    
    # Create strategy instance
    strategy = DualMAStrategy(
        client=client,
        quote_processor=quote_processor,
        symbols=symbols,
        historical_loader=HistoricalDataLoader(client),
        fast_window=args.fast_window,
        slow_window=args.slow_window
    )
    
    # Subscribe to market data
    quote_processor.subscribe(symbols)
    
    # Start realtime data processing
    quote_processor.start()
    
    # Optional: Create time bar aggregator for intraday bars
    bar_aggregator = TimeBarAggregator(quote_processor, interval_seconds=60)  # 1-minute bars
    
    # Create algo execution manager (for TWAP/VWAP)
    algo_manager = AlgoExecutionManager(client, quote_processor)
    
    try:
        # Start the strategy
        strategy.start()
        
        logger.info(f"Strategy started with symbols: {symbols}")
        logger.info("Press Ctrl+C to stop...")
        
        # Keep running until interrupted
        while True:
            # Check risk metrics periodically
            risk_status = risk_manager.check_portfolio_risk()
            
            # Apply portfolio-wide risk controls if needed
            if risk_status["recommendation"] != "normal":
                logger.warning(f"Risk alert: {risk_status['recommendation']}")
                risk_manager.portfolio_risk_control()
                
            # Sleep to avoid CPU usage
            time.sleep(10)
            
    except KeyboardInterrupt:
        logger.info("Stopping strategy...")
    finally:
        # Clean shutdown
        strategy.stop()
        quote_processor.stop()
        logger.info("Strategy stopped")

def print_account_info(args):
    """Print account information"""
    logger.info("Fetching account information")
    
    client = LongPortClient()
    
    # Get account balance
    try:
        balance_info = client.get_account_balance()
        logger.info("Account Balance:")
        
        # 直接打印对象，避免使用 __dict__
        if balance_info:
            if isinstance(balance_info, list):
                for item in balance_info:
                    logger.info(f"{item}")
            else:
                logger.info(f"{balance_info}")
        else:
            logger.info("No balance information available.")
    except Exception as e:
        logger.error(f"Error getting account balance: {e}")
            
    # Get positions
    try:
        positions_response = client.get_positions()
        logger.info("\nCurrent Positions:")
        
        # 处理 StockPositionsResponse 结构
        if hasattr(positions_response, 'channels'):
            has_positions = False
            
            # 遍历账户通道
            for channel in positions_response.channels:
                channel_name = getattr(channel, 'account_channel', 'Unknown')
                
                # 获取该通道下的持仓列表
                if hasattr(channel, 'positions'):
                    positions = channel.positions
                    
                    # 如果有持仓，显示
                    if positions and len(positions) > 0:
                        has_positions = True
                        logger.info(f"Channel: {channel_name}")
                        
                        for position in positions:
                            # 获取持仓详情
                            symbol = getattr(position, 'symbol', 'Unknown')
                            quantity = getattr(position, 'quantity', 'Unknown')
                            avg_price = getattr(position, 'avg_price', 'Unknown')
                            market_value = getattr(position, 'market_value', 'Unknown')
                            
                            logger.info(f"  {symbol}: {quantity} shares @ {avg_price}, Value: {market_value}")
            
            if not has_positions:
                logger.info("No positions found in any account channel.")
        else:
            logger.info(f"Unexpected positions response format: {positions_response}")
            
    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        
    # Get today's orders
    try:
        orders = client.get_today_orders()
        logger.info("\nToday's Orders:")
        if orders:
            for order in orders:
                # 直接打印订单对象，显示所有信息
                logger.info(f"{order}")
                
                # 为了更好的格式化，尝试单独提取一些关键信息
                try:
                    order_id = getattr(order, 'order_id', 'Unknown')
                    symbol = getattr(order, 'symbol', 'Unknown')
                    quantity = getattr(order, 'quantity', 'Unknown')
                    executed_quantity = getattr(order, 'executed_quantity', 'Unknown')
                    price = getattr(order, 'price', 'Unknown')
                    submitted_at = getattr(order, 'submitted_at', 'Unknown')
                    status = getattr(order, 'status', 'Unknown')
                    side = getattr(order, 'side', 'Unknown')
                    order_type = getattr(order, 'order_type', 'Unknown')
                    
                    logger.info(f"  订单ID: {order_id}")
                    logger.info(f"  股票: {symbol}, 方向: {side}, 类型: {order_type}")
                    logger.info(f"  数量: {quantity}, 已成交: {executed_quantity}, 状态: {status}")
                    logger.info(f"  价格: {price}, 创建时间: {submitted_at}")
                    logger.info("  ------------------------")
                except Exception as parse_e:
                    logger.error(f"Error parsing order details: {parse_e}")
        else:
            logger.info("No orders found for today.")
    except Exception as e:
        logger.error(f"Error getting today's orders: {e}")

def create_sample_order(args):
    """Create a sample order to test API"""
    if not args.symbol:
        logger.error("Symbol is required for creating an order")
        return
        
    logger.info(f"Creating sample order for {args.symbol}")
    
    client = LongPortClient()
    
    # Default to a small market buy order
    side = OrderSide.Buy if not args.sell else OrderSide.Sell
    order_type = OrderType.MO if args.market else OrderType.LO
    quantity = args.quantity or 1
    
    # For limit orders, we need a price
    submitted_price = None
    if order_type == OrderType.LO:
        if args.price:
            submitted_price = Decimal(str(args.price))
        else:
            # Get current market price and add/subtract 1% for limit price
            quote = client.quote_ctx.quote([args.symbol])
            if quote:
                price = quote[0].last_done
                if side == OrderSide.Buy:
                    submitted_price = Decimal(str(price * 0.99))  # 1% below market
                else:
                    submitted_price = Decimal(str(price * 1.01))  # 1% above market
                    
    try:
        # Submit the order
        result = client.create_order(
            symbol=args.symbol,
            order_type=order_type,
            side=side,
            quantity=quantity,
            time_in_force=TimeInForceType.Day,
            submitted_price=submitted_price,
            remark="Sample order from CLI"
        )
        
        logger.info(f"Order created: ID {result.order_id}")
        
    except Exception as e:
        logger.error(f"Failed to create order: {e}")

def run_algo_execution(args):
    """Run algorithmic execution (TWAP/VWAP)"""
    if not args.symbol:
        logger.error("Symbol is required for algo execution")
        return
        
    if not args.quantity:
        logger.error("Quantity is required for algo execution")
        return
        
    logger.info(f"Starting {args.algo} execution for {args.symbol}")
    
    client = LongPortClient()
    quote_processor = QuoteProcessor(client)
    
    # Start quote processor
    quote_processor.start()
    
    # Subscribe to symbol
    quote_processor.subscribe([args.symbol])
    
    # Create execution manager
    algo_manager = AlgoExecutionManager(client, quote_processor)
    
    # Default parameters
    side = OrderSide.Buy if not args.sell else OrderSide.Sell
    duration = args.duration or 300  # 5 minutes default
    slices = args.slices or 10
    price_limit = Decimal(str(args.price)) if args.price else None
    
    try:
        # Create the execution
        if args.algo.upper() == "TWAP":
            exec_id = algo_manager.create_twap(
                symbol=args.symbol,
                side=side,
                total_quantity=args.quantity,
                duration_seconds=duration,
                num_slices=slices,
                price_limit=price_limit
            )
        elif args.algo.upper() == "VWAP":
            exec_id = algo_manager.create_vwap(
                symbol=args.symbol,
                side=side,
                total_quantity=args.quantity,
                duration_seconds=duration,
                num_slices=slices,
                price_limit=price_limit
            )
        else:
            logger.error(f"Unknown algorithm: {args.algo}")
            return
            
        if exec_id:
            logger.info(f"Started {args.algo} execution: {exec_id}")
            
            # Monitor execution
            logger.info("Monitoring execution progress (Ctrl+C to stop)...")
            try:
                while True:
                    status = algo_manager.get_execution_status(exec_id)
                    if status:
                        logger.info(
                            f"Progress: {status['progress_pct']:.1f}% - "
                            f"Executed: {status['executed_quantity']}/{status['total_quantity']} @ "
                            f"avg {status['avg_price']:.4f}"
                        )
                        
                        if not status['is_running']:
                            logger.info("Execution completed")
                            break
                            
                    time.sleep(2)
            except KeyboardInterrupt:
                logger.info("Stopping execution monitoring (execution continues in background)")
        else:
            logger.error("Failed to start algorithm execution")
            
    except Exception as e:
        logger.error(f"Error in algorithm execution: {e}")
    finally:
        # Stop quote processor
        quote_processor.stop()

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='LongBridge Quant Trading System')
    
    # Common arguments
    parser.add_argument('--symbols', nargs='+', help='Trading symbols (e.g., AAPL.US GOOG.US)')
    parser.add_argument('--capital', type=float, default=100000.0, help='Initial capital')
    parser.add_argument('--fast-window', type=int, default=5, help='Fast MA window')
    parser.add_argument('--slow-window', type=int, default=20, help='Slow MA window')
    
    # Create subparsers for different modes
    subparsers = parser.add_subparsers(dest='mode', help='Operating mode')
    
    # Backtest mode
    backtest_parser = subparsers.add_parser('backtest', help='Run in backtest mode')
    
    # Live trading mode
    live_parser = subparsers.add_parser('live', help='Run in live trading mode')
    
    # Account info mode
    info_parser = subparsers.add_parser('info', help='Display account information')
    
    # Order creation mode
    order_parser = subparsers.add_parser('order', help='Create a sample order')
    order_parser.add_argument('--symbol', type=str, help='Symbol to trade')
    order_parser.add_argument('--quantity', type=int, help='Order quantity')
    order_parser.add_argument('--price', type=float, help='Limit price (for limit orders)')
    order_parser.add_argument('--market', action='store_true', help='Use market order')
    order_parser.add_argument('--sell', action='store_true', help='Sell instead of buy')
    
    # Algo execution mode
    algo_parser = subparsers.add_parser('algo', help='Run algorithmic execution')
    algo_parser.add_argument('--algo', type=str, choices=['TWAP', 'VWAP'], default='TWAP', help='Algorithm to use')
    algo_parser.add_argument('--symbol', type=str, required=True, help='Symbol to trade')
    algo_parser.add_argument('--quantity', type=int, required=True, help='Total quantity')
    algo_parser.add_argument('--duration', type=int, help='Duration in seconds')
    algo_parser.add_argument('--slices', type=int, help='Number of slices')
    algo_parser.add_argument('--price', type=float, help='Limit price')
    algo_parser.add_argument('--sell', action='store_true', help='Sell instead of buy')
    
    args = parser.parse_args()
    
    # Load environment variables
    load_environment()
    
    # Determine which mode to run
    if args.mode == 'backtest':
        run_backtest(args)
    elif args.mode == 'live':
        run_live_trading(args)
    elif args.mode == 'info':
        print_account_info(args)
    elif args.mode == 'order':
        create_sample_order(args)
    elif args.mode == 'algo':
        run_algo_execution(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main() 