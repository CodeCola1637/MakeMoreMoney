"""
Risk management module for portfolio-level controls
"""
import os
import logging
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np
from datetime import datetime

from ..api_client.client import LongPortClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("risk_manager")

class RiskManager:
    """
    Portfolio risk management and position sizing
    """
    
    def __init__(self, client: LongPortClient):
        self.client = client
        
        # Load risk parameters from environment variables
        self.max_position_size = float(os.getenv("MAX_POSITION_SIZE", 10000))
        self.max_drawdown_pct = float(os.getenv("MAX_DRAWDOWN_PCT", 5))
        self.trailing_stop_pct = float(os.getenv("TRAILING_STOP_PCT", 5))
        
        # Track portfolio metrics
        self.portfolio_value = 0.0
        self.initial_portfolio_value = 0.0
        self.highest_portfolio_value = 0.0
        self.positions = {}
        self.position_history = []
        
        # Performance tracking
        self.max_drawdown = 0.0
        self.current_drawdown = 0.0
        
    def initialize(self):
        """Initialize risk manager with current account state"""
        # Get account balance
        try:
            balance = self.client.get_account_balance()
            
            if balance:
                # Use net equity as portfolio value
                self.portfolio_value = balance.net_equity
                self.initial_portfolio_value = self.portfolio_value
                self.highest_portfolio_value = self.portfolio_value
                
                logger.info(f"Initial portfolio value: {self.portfolio_value}")
                
            # Get current positions
            positions = self.client.get_positions()
            
            if positions:
                for position in positions:
                    self.positions[position.symbol] = {
                        "quantity": position.quantity,
                        "cost_price": position.avg_price,
                        "market_value": position.market_value
                    }
                    
                logger.info(f"Loaded {len(self.positions)} existing positions")
                
        except Exception as e:
            logger.error(f"Error initializing risk manager: {e}")
    
    def update_portfolio_value(self):
        """Update current portfolio value from account balance"""
        try:
            balance = self.client.get_account_balance()
            
            if balance:
                prev_value = self.portfolio_value
                self.portfolio_value = balance.net_equity
                
                # Update highest value if needed
                if self.portfolio_value > self.highest_portfolio_value:
                    self.highest_portfolio_value = self.portfolio_value
                    
                # Calculate drawdown
                self.current_drawdown = 1 - (self.portfolio_value / self.highest_portfolio_value)
                self.max_drawdown = max(self.max_drawdown, self.current_drawdown)
                
                # Log significant changes
                pct_change = (self.portfolio_value / prev_value - 1) * 100 if prev_value > 0 else 0
                if abs(pct_change) >= 0.5:  # Log changes >= 0.5%
                    logger.info(
                        f"Portfolio value: {self.portfolio_value:.2f} ({pct_change:+.2f}%), "
                        f"Drawdown: {self.current_drawdown:.2%}"
                    )
                    
                return self.portfolio_value
                
        except Exception as e:
            logger.error(f"Error updating portfolio value: {e}")
            
        return self.portfolio_value
        
    def update_positions(self):
        """Update current positions"""
        try:
            positions = self.client.get_positions()
            
            # Reset positions dict
            self.positions = {}
            
            if positions:
                for position in positions:
                    self.positions[position.symbol] = {
                        "quantity": position.quantity,
                        "cost_price": position.avg_price,
                        "market_value": position.market_value
                    }
                    
                # Record position snapshot for analysis
                self.position_history.append({
                    "timestamp": datetime.now(),
                    "positions": self.positions.copy(),
                    "portfolio_value": self.portfolio_value
                })
                
        except Exception as e:
            logger.error(f"Error updating positions: {e}")
            
    def get_position_size(self, symbol: str, price: float) -> int:
        """
        Calculate appropriate position size based on risk parameters
        """
        # Get account balance
        self.update_portfolio_value()
        
        # Calculate portfolio percentage allocation (risk-based)
        risk_pct = 0.01  # Default 1% risk per trade
        
        # Adjust risk based on current drawdown
        if self.current_drawdown > 0.05:  # More than 5% drawdown
            risk_pct *= (1 - self.current_drawdown * 2)  # Reduce risk when in drawdown
            
        # Calculate maximum notional value for this trade
        max_notional = self.portfolio_value * risk_pct
        
        # Calculate number of shares/lots based on price
        size = int(max_notional / price)
        
        # Cap at maximum position size
        size = min(size, int(self.max_position_size))
        
        return max(1, size)  # Ensure at least 1 unit
        
    def check_portfolio_risk(self) -> Dict[str, Any]:
        """
        Check overall portfolio risk metrics
        Returns risk status and recommendations
        """
        # Update portfolio value
        self.update_portfolio_value()
        
        # Check if drawdown exceeds limit
        excessive_drawdown = self.current_drawdown * 100 > self.max_drawdown_pct
        
        # Calculate portfolio beta (simplified)
        portfolio_beta = self._estimate_portfolio_beta()
        high_beta = portfolio_beta > 1.2
        
        # Calculate concentration risk
        concentration = self._calculate_concentration()
        high_concentration = concentration > 0.5  # More than 50% in a single position
        
        # Prepare risk assessment
        risk_status = {
            "excessive_drawdown": excessive_drawdown,
            "drawdown_pct": self.current_drawdown * 100,
            "high_beta": high_beta,
            "portfolio_beta": portfolio_beta,
            "high_concentration": high_concentration,
            "concentration": concentration,
            "portfolio_value": self.portfolio_value,
            "max_drawdown_pct": self.max_drawdown * 100,
            "recommendation": "normal"
        }
        
        # Generate recommendation based on risk factors
        if excessive_drawdown:
            risk_status["recommendation"] = "reduce_exposure"
            logger.warning(
                f"RISK WARNING: Excessive drawdown {self.current_drawdown:.2%} > "
                f"{self.max_drawdown_pct:.1f}%"
            )
        elif high_beta:
            risk_status["recommendation"] = "reduce_beta"
            logger.warning(
                f"RISK WARNING: High portfolio beta {portfolio_beta:.2f}"
            )
        elif high_concentration:
            risk_status["recommendation"] = "diversify"
            logger.warning(
                f"RISK WARNING: High position concentration {concentration:.2%}"
            )
            
        return risk_status
        
    def trailing_stop_loss(self, symbol: str, current_price: float, highest_price: float, is_long: bool = True) -> bool:
        """
        Check if a trailing stop loss should be triggered
        Returns True if stop loss triggered
        """
        # For long positions
        if is_long:
            stop_price = highest_price * (1 - self.trailing_stop_pct / 100)
            return current_price < stop_price
        
        # For short positions
        else:
            stop_price = highest_price * (1 + self.trailing_stop_pct / 100)
            return current_price > stop_price
            
    def _estimate_portfolio_beta(self) -> float:
        """
        Simple estimate of portfolio beta
        A more accurate implementation would use proper regression against market index
        """
        # Placeholder for simplified beta calculation
        # In a real implementation, this would regress returns against index
        return 1.0
        
    def _calculate_concentration(self) -> float:
        """
        Calculate position concentration risk
        Returns the largest position as % of portfolio
        """
        if not self.positions or self.portfolio_value == 0:
            return 0.0
            
        # Find largest position by market value
        largest_position = max(
            pos.get("market_value", 0) for pos in self.positions.values()
        )
        
        return largest_position / self.portfolio_value
        
    def generate_risk_report(self) -> Dict[str, Any]:
        """
        Generate comprehensive risk report
        """
        # Update data
        self.update_portfolio_value()
        self.update_positions()
        
        # Calculate basic metrics
        portfolio_return = (self.portfolio_value / self.initial_portfolio_value - 1) * 100
        
        report = {
            "timestamp": datetime.now(),
            "portfolio_value": self.portfolio_value,
            "initial_value": self.initial_portfolio_value,
            "return_pct": portfolio_return,
            "max_drawdown_pct": self.max_drawdown * 100,
            "current_drawdown_pct": self.current_drawdown * 100,
            "position_count": len(self.positions),
            "portfolio_beta": self._estimate_portfolio_beta(),
            "concentration": self._calculate_concentration(),
            "positions": self.positions
        }
        
        return report
        
    def portfolio_risk_control(self) -> Dict[str, Any]:
        """
        Apply portfolio-wide risk control rules
        Returns actions taken
        """
        risk_status = self.check_portfolio_risk()
        actions = {"actions_taken": []}
        
        # If in excessive drawdown, reduce overall exposure
        if risk_status["excessive_drawdown"]:
            actions["actions_taken"].append("reduce_exposure")
            logger.warning(
                f"RISK CONTROL: Excessive drawdown {risk_status['drawdown_pct']:.2f}% > "
                f"{self.max_drawdown_pct}% - reducing exposure"
            )
            
        # If beta too high, adjust leverage
        if risk_status["high_beta"] and risk_status["portfolio_beta"] > 1.2:
            target_beta = 1.0
            actions["actions_taken"].append(f"adjust_beta_to_{target_beta}")
            logger.warning(
                f"RISK CONTROL: High beta {risk_status['portfolio_beta']:.2f} > 1.2 - "
                f"adjusting to target {target_beta}"
            )
            
        return actions 