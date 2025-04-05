"""
Backtesting module with LongPort fee model support
"""
import logging
import os
from typing import Dict, List, Any, Union, Optional, Tuple, Callable
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from decimal import Decimal

from ..strategy.template import BarData
from ..strategy.dual_ma import DualMAStrategy

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backtest")

class LongPortFeeModel:
    """
    LongPort fee model for realistic backtest cost calculation
    
    These are approximations based on general broker fees structure
    and should be adjusted with actual LongPort fees when possible
    """
    
    def __init__(self):
        # Basic commission rates 
        self.hk_commission_rate = 0.0028  # 0.28% for Hong Kong stocks
        self.us_commission_rate = 0.0025  # 0.25% for US stocks
        
        # Platform fees
        self.platform_fee_min_hk = 15.0  # Min platform fee HKD
        self.platform_fee_min_us = 1.99  # Min platform fee USD
        
        # Exchange fees (simplified)
        self.hk_stamp_duty = 0.0013     # 0.13% stamp duty
        self.hk_trading_fee = 0.00005    # 0.005% trading fee
        self.hk_transaction_levy = 0.00027  # 0.027% SFC transaction levy
        
        # US SEC and TAF fees
        self.us_sec_fee = 0.0000229  # Per USD value of sell orders
        self.us_taf_fee = 0.000119   # Per share fee with a maximum of $5.95 USD
        
    def calculate_fees(
        self, 
        symbol: str, 
        price: float, 
        quantity: int, 
        is_buy: bool = True
    ) -> Dict[str, float]:
        """
        Calculate trading fees for a transaction
        
        Returns detailed fee breakdown
        """
        # Determine market (HK or US)
        is_hk = symbol.endswith('.HK')
        is_us = symbol.endswith('.US')
        
        if not (is_hk or is_us):
            # Default to US for unknown markets
            is_us = True
            
        # Calculate transaction value
        value = price * quantity
        
        # Fee structure
        fees = {}
        
        if is_hk:
            # HK fees calculation
            commission = value * self.hk_commission_rate
            commission = max(commission, self.platform_fee_min_hk)
            fees['commission'] = commission
            
            # Other HK fees
            fees['trading_fee'] = value * self.hk_trading_fee
            fees['transaction_levy'] = value * self.hk_transaction_levy
            
            # Stamp duty only applies to buy transactions in HK
            if is_buy:
                fees['stamp_duty'] = value * self.hk_stamp_duty
                
        elif is_us:
            # US fees calculation
            commission = value * self.us_commission_rate
            commission = max(commission, self.platform_fee_min_us)
            fees['commission'] = commission
            
            # SEC fee only applies to sell transactions
            if not is_buy:
                fees['sec_fee'] = value * self.us_sec_fee
                
            # TAF fee (per share with max cap)
            taf = min(quantity * self.us_taf_fee, 5.95)
            fees['taf_fee'] = taf
            
        # Calculate total fees
        fees['total'] = sum(fees.values())
        
        return fees
        
    def apply_fees(
        self, 
        symbol: str, 
        price: float, 
        quantity: int, 
        is_buy: bool = True
    ) -> float:
        """
        Calculate the net price after fees
        
        Returns the effective price including fees
        """
        fees = self.calculate_fees(symbol, price, quantity, is_buy)
        total_fees = fees['total']
        
        # For buy orders, fees increase effective price
        # For sell orders, fees decrease effective price
        if is_buy:
            effective_price = price + (total_fees / quantity)
        else:
            effective_price = price - (total_fees / quantity)
            
        return effective_price


class BacktestEngine:
    """
    Backtesting engine for strategy evaluation
    """
    
    def __init__(
        self,
        strategy_class,
        symbols: List[str],
        start_date: Union[str, datetime],
        end_date: Union[str, datetime],
        initial_capital: float = 100000.0,
        fee_model: Optional[LongPortFeeModel] = None,
        data_source: Optional[Dict[str, pd.DataFrame]] = None,
        **strategy_params
    ):
        self.strategy_class = strategy_class
        self.symbols = symbols
        self.start_date = pd.to_datetime(start_date)
        self.end_date = pd.to_datetime(end_date)
        self.initial_capital = initial_capital
        self.fee_model = fee_model or LongPortFeeModel()
        
        # Strategy parameters
        self.strategy_params = strategy_params
        
        # Data storage
        self.data = data_source or {}
        self.bars = {}
        for symbol in symbols:
            if symbol not in self.data:
                self.data[symbol] = None
                
        # Results tracking
        self.portfolio_value = initial_capital
        self.cash = initial_capital
        self.positions = {symbol: 0 for symbol in symbols}
        self.position_values = {symbol: 0.0 for symbol in symbols}
        
        # Performance metrics
        self.trade_history = []
        self.equity_curve = []
        self.daily_returns = []
        self.metrics = {}
        
        # Internal state
        self.current_date = None
        self.strategy = None
        
    def load_data(self, symbol: str, data: pd.DataFrame):
        """Load historical data for a symbol"""
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            self.positions[symbol] = 0
            self.position_values[symbol] = 0.0
            
        # Process data
        if 'datetime' not in data.columns and 'timestamp' in data.columns:
            data['datetime'] = pd.to_datetime(data['timestamp'])
            
        # Ensure date range
        if 'datetime' in data.columns:
            data = data[(data['datetime'] >= self.start_date) & 
                      (data['datetime'] <= self.end_date)]
                      
        self.data[symbol] = data
        logger.info(f"Loaded {len(data)} bars for {symbol}")
        
    def load_data_from_csv(self, symbol: str, file_path: str):
        """Load data from CSV file"""
        data = pd.read_csv(file_path)
        self.load_data(symbol, data)
        
    def initialize(self):
        """Initialize the backtest"""
        # Align all data on same timeline
        self._align_data()
        
        # Initialize performance tracking
        self.portfolio_value = self.initial_capital
        self.cash = self.initial_capital
        self.positions = {symbol: 0 for symbol in self.symbols}
        self.position_values = {symbol: 0.0 for symbol in self.symbols}
        
        # Reset metrics
        self.trade_history = []
        self.equity_curve = []
        self.daily_returns = []
        self.metrics = {}
        
        # Create mock objects for strategy
        mock_client = None  # No API client in backtest
        mock_quote_processor = None  # No live data in backtest
        
        # Create strategy instance
        self.strategy = self.strategy_class(
            mock_client,
            mock_quote_processor,
            self.symbols,
            **self.strategy_params
        )
        
        # Override strategy execution methods
        self._monkey_patch_strategy()
        
        logger.info(f"Initialized backtest from {self.start_date} to {self.end_date}")
        
    def _align_data(self):
        """Align data from different sources to common timeline"""
        # Check if data is available for all symbols
        for symbol in self.symbols:
            if symbol not in self.data or self.data[symbol] is None:
                logger.warning(f"No data available for {symbol}")
                
        # Find common date range
        common_dates = None
        for symbol in self.symbols:
            if self.data[symbol] is not None:
                dates = pd.to_datetime(self.data[symbol]['datetime']).dt.date.unique()
                if common_dates is None:
                    common_dates = set(dates)
                else:
                    common_dates &= set(dates)
                    
        if common_dates:
            logger.info(f"Found {len(common_dates)} common trading days")
            
            # Create date index
            self.date_index = sorted(common_dates)
            self.current_date = self.date_index[0]
        else:
            logger.warning("No common trading days found")
            
    def _monkey_patch_strategy(self):
        """Override strategy methods for backtest mode"""
        # Store original methods
        self.strategy._original_buy = self.strategy.buy
        self.strategy._original_sell = self.strategy.sell
        
        # Replace with backtest versions
        self.strategy.buy = lambda symbol, price, volume, order_type=None: self._on_buy(symbol, price, volume)
        self.strategy.sell = lambda symbol, price, volume, order_type=None: self._on_sell(symbol, price, volume)
        
    def _on_buy(self, symbol: str, price: float, volume: int):
        """Handle buy orders in backtest"""
        # Calculate total cost with fees
        effective_price = self.fee_model.apply_fees(symbol, price, volume, is_buy=True)
        total_cost = effective_price * volume
        
        # Check if we have enough cash
        if total_cost > self.cash:
            # Adjust volume to available cash
            max_volume = int(self.cash / effective_price)
            if max_volume <= 0:
                logger.warning(
                    f"Insufficient cash for order: {symbol} BUY {volume} @ {price} "
                    f"(Requires: {total_cost:.2f}, Available: {self.cash:.2f})"
                )
                return None
                
            volume = max_volume
            total_cost = effective_price * volume
            logger.warning(
                f"Adjusted order due to cash: {symbol} BUY {volume} @ {price}"
            )
            
        # Execute the trade
        self.positions[symbol] += volume
        self.cash -= total_cost
        
        # Record trade
        trade = {
            'symbol': symbol,
            'side': 'BUY',
            'timestamp': self.current_date,
            'price': price,
            'quantity': volume,
            'effective_price': effective_price,
            'cost': total_cost,
            'fees': total_cost - (price * volume)
        }
        self.trade_history.append(trade)
        
        logger.info(
            f"BACKTEST: Buy {volume} {symbol} @ {price:.4f} "
            f"(Effective: {effective_price:.4f})"
        )
        
        return {
            'order_id': f"BACKTEST-BUY-{len(self.trade_history)}",
            'symbol': symbol,
            'quantity': volume
        }
        
    def _on_sell(self, symbol: str, price: float, volume: int):
        """Handle sell orders in backtest"""
        # Check position size
        current_position = self.positions.get(symbol, 0)
        if volume > current_position:
            if current_position <= 0:
                logger.warning(
                    f"Cannot sell {symbol} - no position"
                )
                return None
                
            # Adjust volume to available position
            volume = current_position
            logger.warning(
                f"Adjusted sell order to available position: {symbol} SELL {volume} @ {price}"
            )
            
        # Calculate proceeds with fees
        effective_price = self.fee_model.apply_fees(symbol, price, volume, is_buy=False)
        total_proceeds = effective_price * volume
        
        # Execute the trade
        self.positions[symbol] -= volume
        self.cash += total_proceeds
        
        # Record trade
        trade = {
            'symbol': symbol,
            'side': 'SELL',
            'timestamp': self.current_date,
            'price': price,
            'quantity': volume,
            'effective_price': effective_price,
            'proceeds': total_proceeds,
            'fees': (price * volume) - total_proceeds
        }
        self.trade_history.append(trade)
        
        logger.info(
            f"BACKTEST: Sell {volume} {symbol} @ {price:.4f} "
            f"(Effective: {effective_price:.4f})"
        )
        
        return {
            'order_id': f"BACKTEST-SELL-{len(self.trade_history)}",
            'symbol': symbol,
            'quantity': volume
        }
        
    def run(self):
        """Run the backtest"""
        if not hasattr(self, 'date_index'):
            self.initialize()
            
        if not self.date_index:
            logger.error("No trading days available for backtest")
            return
            
        logger.info(f"Starting backtest with {len(self.date_index)} trading days")
        
        # Start the strategy
        self.strategy.on_start()
        
        # Process each trading day
        for date in self.date_index:
            self.current_date = date
            
            # Process each symbol
            bar_dict = {}
            for symbol in self.symbols:
                if self.data[symbol] is not None:
                    # Get data for this day
                    day_data = self.data[symbol][self.data[symbol]['datetime'].dt.date == date]
                    
                    if not day_data.empty:
                        # Create bar object
                        for _, row in day_data.iterrows():
                            bar = BarData(
                                symbol=symbol,
                                open_price=row['open'],
                                high_price=row['high'],
                                low_price=row['low'],
                                close_price=row['close'],
                                volume=row['volume'],
                                turnover=row.get('turnover', 0),
                                timestamp=row['datetime']
                            )
                            bar_dict[symbol] = bar
                            
                            # Process the bar
                            self.strategy.on_bar(bar)
                            
            # Update portfolio value
            self._update_portfolio_value(bar_dict)
            
        # Store final metrics
        self._calculate_metrics()
        
        logger.info(f"Backtest completed: Final portfolio value: {self.portfolio_value:.2f}")
        
        return self.metrics
        
    def _update_portfolio_value(self, bar_dict: Dict[str, BarData]):
        """Update portfolio value at the end of the day"""
        # Calculate position values
        equity = self.cash
        
        for symbol, position in self.positions.items():
            if position != 0 and symbol in bar_dict:
                # Use closing price for valuation
                price = bar_dict[symbol].close_price
                value = price * position
                self.position_values[symbol] = value
                equity += value
                
        # Record equity curve
        self.portfolio_value = equity
        self.equity_curve.append({
            'date': self.current_date,
            'equity': equity,
            'cash': self.cash,
            'positions': self.positions.copy(),
            'position_values': self.position_values.copy()
        })
        
        # Calculate daily return
        if len(self.equity_curve) > 1:
            prev_equity = self.equity_curve[-2]['equity']
            daily_return = (equity / prev_equity) - 1.0
            self.daily_returns.append(daily_return)
            
    def _calculate_metrics(self):
        """Calculate performance metrics"""
        # Convert equity curve to DataFrame
        equity_df = pd.DataFrame(self.equity_curve)
        equity_df.set_index('date', inplace=True)
        
        # Get daily returns series
        returns = pd.Series(self.daily_returns, index=equity_df.index[1:])
        
        # Calculate basic metrics
        total_return = (self.portfolio_value / self.initial_capital) - 1.0
        total_days = len(self.equity_curve)
        total_trades = len(self.trade_history)
        
        # Calculate win/loss ratio
        trades_df = pd.DataFrame(self.trade_history)
        if not trades_df.empty:
            buy_trades = trades_df[trades_df['side'] == 'BUY'].copy()
            sell_trades = trades_df[trades_df['side'] == 'SELL'].copy()
            
            # Calculate P&L for round trips
            pnl_list = []
            for symbol in self.symbols:
                symbol_buys = buy_trades[buy_trades['symbol'] == symbol]
                symbol_sells = sell_trades[sell_trades['symbol'] == symbol]
                
                if symbol_buys.empty or symbol_sells.empty:
                    continue
                    
                # Match buys and sells (simplified FIFO)
                buy_queue = []
                for _, buy in symbol_buys.iterrows():
                    buy_queue.append((buy['timestamp'], buy['price'], buy['quantity']))
                    
                for _, sell in symbol_sells.iterrows():
                    sell_qty = sell['quantity']
                    sell_price = sell['price']
                    
                    while sell_qty > 0 and buy_queue:
                        buy_time, buy_price, buy_qty = buy_queue[0]
                        
                        if buy_qty <= sell_qty:
                            # Consume entire buy
                            traded_qty = buy_qty
                            buy_queue.pop(0)
                        else:
                            # Partial buy
                            traded_qty = sell_qty
                            buy_queue[0] = (buy_time, buy_price, buy_qty - sell_qty)
                            
                        # Calculate P&L for this trade
                        pnl = (sell_price - buy_price) * traded_qty
                        pnl_list.append(pnl)
                        
                        sell_qty -= traded_qty
                        
            # Calculate win/loss stats
            if pnl_list:
                wins = sum(1 for pnl in pnl_list if pnl > 0)
                losses = sum(1 for pnl in pnl_list if pnl < 0)
                
                win_rate = wins / len(pnl_list) if pnl_list else 0.0
                
                avg_win = np.mean([pnl for pnl in pnl_list if pnl > 0]) if wins else 0.0
                avg_loss = np.mean([abs(pnl) for pnl in pnl_list if pnl < 0]) if losses else 0.0
                
                profit_factor = (avg_win * wins) / (avg_loss * losses) if losses and avg_loss else float('inf')
            else:
                win_rate = 0.0
                avg_win = 0.0
                avg_loss = 0.0
                profit_factor = 0.0
        else:
            win_rate = 0.0
            avg_win = 0.0
            avg_loss = 0.0
            profit_factor = 0.0
            
        # Calculate financial ratios
        if len(returns) > 0:
            sharpe_ratio = np.sqrt(252) * returns.mean() / returns.std() if returns.std() > 0 else 0.0
            max_drawdown = self._calculate_max_drawdown(equity_df['equity'])
            
            # Calculate annualized return
            days = (self.end_date - self.start_date).days
            years = days / 365.0
            annualized_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0
        else:
            sharpe_ratio = 0.0
            max_drawdown = 0.0
            annualized_return = 0.0
            
        # Store metrics
        self.metrics = {
            'initial_capital': self.initial_capital,
            'final_equity': self.portfolio_value,
            'total_return_pct': total_return * 100,
            'annualized_return_pct': annualized_return * 100,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown_pct': max_drawdown * 100,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'average_win': avg_win,
            'average_loss': avg_loss,
            'trading_days': total_days
        }
        
        return self.metrics
        
    def _calculate_max_drawdown(self, equity: pd.Series) -> float:
        """Calculate maximum drawdown from equity curve"""
        running_max = equity.cummax()
        drawdown = (equity / running_max) - 1
        return float(drawdown.min())
        
    def plot_results(self):
        """Plot backtest results"""
        if not self.equity_curve:
            logger.warning("No equity curve data available for plotting")
            return
            
        # Create figure with multiple subplots
        fig, axs = plt.subplots(3, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1, 1]})
        
        # Convert equity curve to DataFrame
        equity_df = pd.DataFrame(self.equity_curve)
        
        # Plot equity curve
        ax1 = axs[0]
        ax1.plot(equity_df['date'], equity_df['equity'], label='Portfolio Value')
        ax1.set_title('Backtest Results')
        ax1.set_ylabel('Portfolio Value')
        ax1.grid(True)
        ax1.legend()
        
        # Plot daily returns
        if self.daily_returns:
            returns_dates = equity_df['date'].iloc[1:].values
            ax2 = axs[1]
            ax2.bar(returns_dates, self.daily_returns, label='Daily Returns')
            ax2.set_ylabel('Daily Return')
            ax2.grid(True)
            
        # Plot drawdown
        equity = pd.Series(equity_df['equity'].values, index=equity_df['date'])
        running_max = equity.cummax()
        drawdown = (equity / running_max) - 1
        
        ax3 = axs[2]
        ax3.fill_between(equity_df['date'], 0, drawdown * 100, color='red', alpha=0.3, label='Drawdown')
        ax3.set_ylabel('Drawdown %')
        ax3.set_xlabel('Date')
        ax3.grid(True)
        
        # Add summary statistics
        stats_text = (
            f"Total Return: {self.metrics['total_return_pct']:.2f}%\n"
            f"Annual Return: {self.metrics['annualized_return_pct']:.2f}%\n"
            f"Sharpe Ratio: {self.metrics['sharpe_ratio']:.2f}\n"
            f"Max Drawdown: {self.metrics['max_drawdown_pct']:.2f}%\n"
            f"Win Rate: {self.metrics['win_rate']*100:.1f}%"
        )
        
        fig.text(0.01, 0.01, stats_text, fontsize=10, verticalalignment='bottom')
        
        plt.tight_layout()
        
        # Save and show
        plt.savefig('backtest_results.png')
        plt.show()
        
    def generate_report(self, output_file: str = 'backtest_report.html'):
        """Generate HTML report with detailed analysis"""
        # TODO: Implement HTML report generation with charts
        pass


# Utility function to create a dual MA strategy backtest
def run_dual_ma_backtest(
    symbols: List[str],
    data_source: Dict[str, pd.DataFrame],
    start_date: str,
    end_date: str,
    fast_window: int = 5,
    slow_window: int = 20,
    initial_capital: float = 100000.0,
    plot_results: bool = True
) -> Dict[str, Any]:
    """
    Utility function to run a dual MA strategy backtest
    """
    engine = BacktestEngine(
        DualMAStrategy,
        symbols,
        start_date,
        end_date,
        initial_capital=initial_capital,
        fast_window=fast_window,
        slow_window=slow_window
    )
    
    # Load data
    for symbol, data in data_source.items():
        if symbol in symbols:
            engine.load_data(symbol, data)
    
    # Run backtest
    engine.run()
    
    # Plot if requested
    if plot_results:
        engine.plot_results()
        
    return engine.metrics 