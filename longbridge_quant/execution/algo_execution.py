"""
Algorithmic order execution module supporting TWAP and VWAP algorithms
"""
import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union, Callable
from decimal import Decimal
import numpy as np
import pandas as pd

from longport.openapi import OrderSide, OrderType, TimeInForceType, PushQuote

from ..api_client.client import LongPortClient
from ..data_engine.realtime import QuoteProcessor

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("algo_execution")

class AlgoExecutionBase:
    """
    Base class for algorithmic order execution
    """
    
    def __init__(
        self,
        client: LongPortClient,
        quote_processor: QuoteProcessor,
        symbol: str,
        side: OrderSide,
        total_quantity: int,
        duration_seconds: int,
        price_limit: Optional[float] = None,
        on_complete: Optional[Callable] = None
    ):
        self.client = client
        self.quote_processor = quote_processor
        self.symbol = symbol
        self.side = side
        self.total_quantity = total_quantity
        self.duration_seconds = duration_seconds
        self.price_limit = price_limit
        self.on_complete = on_complete
        
        # Execution state
        self.executed_quantity = 0
        self.remaining_quantity = total_quantity
        self.orders = []
        self.start_time = None
        self.end_time = None
        self.is_running = False
        self.execution_thread = None
        self.avg_execution_price = 0.0
        
        # Market data
        self.last_price = 0.0
        self.quote_processor.register_price_callback(self._on_quote_update)
    
    def _on_quote_update(self, symbol: str, quote: PushQuote):
        """Handle market data updates"""
        if symbol == self.symbol:
            self.last_price = quote.last_done
            
    def start(self):
        """Start the execution algorithm"""
        if self.is_running:
            logger.warning(f"Algorithm already running for {self.symbol}")
            return False
            
        self.is_running = True
        self.start_time = datetime.now()
        self.end_time = self.start_time + timedelta(seconds=self.duration_seconds)
        
        logger.info(
            f"Starting {self.__class__.__name__} for {self.symbol}: "
            f"{self.side.name} {self.total_quantity} units over {self.duration_seconds}s"
        )
        
        # Start execution in a separate thread
        self.execution_thread = threading.Thread(target=self._execute_algo)
        self.execution_thread.daemon = True
        self.execution_thread.start()
        
        return True
        
    def stop(self):
        """Stop the execution algorithm"""
        if not self.is_running:
            return
            
        self.is_running = False
        if self.execution_thread:
            self.execution_thread.join(timeout=2.0)
            
        logger.info(
            f"Stopped {self.__class__.__name__} for {self.symbol}: "
            f"Executed {self.executed_quantity}/{self.total_quantity} units"
        )
        
    def _execute_algo(self):
        """
        Main execution loop, to be implemented by subclasses
        """
        pass
        
    def _send_order(self, quantity: int, price: Optional[float] = None):
        """Send an order to the market"""
        if not self.is_running or quantity <= 0:
            return None
            
        try:
            # Determine order type and price
            if price is None and self.price_limit is None:
                # Market order
                order_type = OrderType.MO
                submitted_price = None
            else:
                # Limit order
                order_type = OrderType.LO
                submitted_price = Decimal(str(price if price is not None else self.price_limit))
                
            # Create the order    
            order_result = self.client.create_order(
                symbol=self.symbol,
                order_type=order_type,
                side=self.side,
                quantity=quantity,
                time_in_force=TimeInForceType.Day,
                submitted_price=submitted_price,
                remark="Algo execution"
            )
            
            if order_result:
                logger.info(
                    f"Placed order: {self.symbol} {self.side.name} {quantity} @ "
                    f"{submitted_price if submitted_price else 'Market'} - {order_result.order_id}"
                )
                
                self.orders.append({
                    "order_id": order_result.order_id,
                    "quantity": quantity,
                    "price": float(submitted_price) if submitted_price else None,
                    "status": "submitted",
                    "timestamp": datetime.now()
                })
                
                # Update execution tracking
                self.executed_quantity += quantity
                self.remaining_quantity -= quantity
                
                # Update average execution price (simple calculation)
                if submitted_price:
                    self.avg_execution_price = (
                        (self.avg_execution_price * (self.executed_quantity - quantity) + 
                         float(submitted_price) * quantity) / self.executed_quantity
                    )
                
                return order_result
                
        except Exception as e:
            logger.error(f"Error sending order for {self.symbol}: {e}")
            
        return None
        
    def get_execution_status(self) -> Dict[str, Any]:
        """Get current execution status"""
        now = datetime.now()
        progress = 0.0
        
        if self.start_time:
            elapsed = (now - self.start_time).total_seconds()
            progress = min(100.0, (elapsed / self.duration_seconds) * 100)
            
        return {
            "symbol": self.symbol,
            "side": self.side.name,
            "total_quantity": self.total_quantity,
            "executed_quantity": self.executed_quantity,
            "remaining_quantity": self.remaining_quantity,
            "progress_pct": progress,
            "avg_price": self.avg_execution_price,
            "is_running": self.is_running,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "order_count": len(self.orders)
        }


class TWAPExecution(AlgoExecutionBase):
    """
    Time-Weighted Average Price (TWAP) execution algorithm
    Divides the order evenly across time slices
    """
    
    def __init__(
        self,
        client: LongPortClient,
        quote_processor: QuoteProcessor,
        symbol: str,
        side: OrderSide,
        total_quantity: int,
        duration_seconds: int,
        num_slices: int = 10,
        price_limit: Optional[float] = None,
        price_offset_pct: float = 0.0,
        on_complete: Optional[Callable] = None
    ):
        super().__init__(
            client, quote_processor, symbol, side, total_quantity, duration_seconds, price_limit, on_complete
        )
        
        self.num_slices = num_slices
        self.price_offset_pct = price_offset_pct
        
        # Calculate time and size per slice
        self.time_per_slice = duration_seconds / num_slices
        self.qty_per_slice = total_quantity // num_slices
        
        # Handle remainder
        self.qty_remainder = total_quantity % num_slices
        
    def _execute_algo(self):
        """Execute TWAP algorithm"""
        slice_count = 0
        
        while self.is_running and slice_count < self.num_slices:
            # Calculate quantity for this slice
            qty = self.qty_per_slice
            if slice_count < self.qty_remainder:
                qty += 1
                
            # Skip if no quantity to trade
            if qty <= 0:
                slice_count += 1
                continue
                
            # Wait for the right time for this slice
            target_time = self.start_time + timedelta(seconds=self.time_per_slice * slice_count)
            now = datetime.now()
            
            if now < target_time:
                wait_seconds = (target_time - now).total_seconds()
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                    
            # Determine price if using limit orders
            price = None
            if self.price_limit is not None or self.price_offset_pct != 0.0:
                # Use current market price with offset
                if self.last_price > 0:
                    offset_factor = 1.0
                    if self.price_offset_pct != 0.0:
                        # Add to bids, subtract from offers
                        if self.side == OrderSide.Buy:
                            offset_factor -= self.price_offset_pct / 100.0
                        else:
                            offset_factor += self.price_offset_pct / 100.0
                            
                    price = self.last_price * offset_factor
                    
                    # Apply price limit if specified
                    if self.price_limit is not None:
                        if self.side == OrderSide.Buy:
                            price = min(price, self.price_limit)
                        else:
                            price = max(price, self.price_limit)
                            
            # Send the order
            self._send_order(qty, price)
            
            # Increment slice counter
            slice_count += 1
            
            # Check if we're past the end time
            if datetime.now() >= self.end_time:
                break
                
        # Complete the execution
        self.is_running = False
        
        if self.on_complete:
            try:
                self.on_complete(self.get_execution_status())
            except Exception as e:
                logger.error(f"Error in TWAP completion callback: {e}")
                
        logger.info(
            f"TWAP execution completed: {self.symbol} {self.side.name} "
            f"{self.executed_quantity}/{self.total_quantity} @ avg {self.avg_execution_price:.4f}"
        )


class VWAPExecution(AlgoExecutionBase):
    """
    Volume-Weighted Average Price (VWAP) execution algorithm
    Distributes the order based on historical volume profile
    """
    
    def __init__(
        self,
        client: LongPortClient,
        quote_processor: QuoteProcessor,
        symbol: str,
        side: OrderSide,
        total_quantity: int,
        duration_seconds: int,
        historical_volume_profile: Optional[List[float]] = None,
        num_slices: int = 10,
        price_limit: Optional[float] = None,
        price_offset_pct: float = 0.0,
        on_complete: Optional[Callable] = None
    ):
        super().__init__(
            client, quote_processor, symbol, side, total_quantity, duration_seconds, price_limit, on_complete
        )
        
        self.num_slices = num_slices
        self.price_offset_pct = price_offset_pct
        
        # Use provided volume profile or default to uniform
        if historical_volume_profile is not None and len(historical_volume_profile) == num_slices:
            self.volume_profile = historical_volume_profile
        else:
            # Default to uniform distribution
            self.volume_profile = [1.0 / num_slices] * num_slices
            
        # Calculate quantities per slice based on volume profile
        total_weight = sum(self.volume_profile)
        normalized_weights = [w / total_weight for w in self.volume_profile]
        
        self.slice_quantities = []
        remaining = total_quantity
        
        for i in range(num_slices - 1):
            qty = int(total_quantity * normalized_weights[i])
            self.slice_quantities.append(qty)
            remaining -= qty
            
        # Put the remainder in the last slice
        self.slice_quantities.append(remaining)
        
        # Calculate time per slice
        self.time_per_slice = duration_seconds / num_slices
        
        # Track volume and price data
        self.volume_data = []
        
    def _execute_algo(self):
        """Execute VWAP algorithm"""
        slice_count = 0
        
        while self.is_running and slice_count < self.num_slices:
            # Get quantity for this slice
            qty = self.slice_quantities[slice_count]
            
            # Skip if no quantity to trade
            if qty <= 0:
                slice_count += 1
                continue
                
            # Wait for the right time for this slice
            target_time = self.start_time + timedelta(seconds=self.time_per_slice * slice_count)
            now = datetime.now()
            
            if now < target_time:
                wait_seconds = (target_time - now).total_seconds()
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                    
            # Determine price if using limit orders
            price = None
            if self.price_limit is not None or self.price_offset_pct != 0.0:
                # Use current market price with offset
                if self.last_price > 0:
                    offset_factor = 1.0
                    if self.price_offset_pct != 0.0:
                        # Add to bids, subtract from offers
                        if self.side == OrderSide.Buy:
                            offset_factor -= self.price_offset_pct / 100.0
                        else:
                            offset_factor += self.price_offset_pct / 100.0
                            
                    price = self.last_price * offset_factor
                    
                    # Apply price limit if specified
                    if self.price_limit is not None:
                        if self.side == OrderSide.Buy:
                            price = min(price, self.price_limit)
                        else:
                            price = max(price, self.price_limit)
                            
            # Send the order
            self._send_order(qty, price)
            
            # Record volume data
            self.volume_data.append({
                "slice": slice_count,
                "time": datetime.now(),
                "quantity": qty,
                "price": price or self.last_price
            })
            
            # Increment slice counter
            slice_count += 1
            
            # Check if we're past the end time
            if datetime.now() >= self.end_time:
                break
                
        # Complete the execution
        self.is_running = False
        
        if self.on_complete:
            try:
                self.on_complete(self.get_execution_status())
            except Exception as e:
                logger.error(f"Error in VWAP completion callback: {e}")
                
        logger.info(
            f"VWAP execution completed: {self.symbol} {self.side.name} "
            f"{self.executed_quantity}/{self.total_quantity} @ avg {self.avg_execution_price:.4f}"
        )
        
    def calculate_vwap(self) -> float:
        """Calculate the volume-weighted average price"""
        if not self.volume_data:
            return 0.0
            
        volume_price_sum = sum(d["quantity"] * d["price"] for d in self.volume_data)
        total_volume = sum(d["quantity"] for d in self.volume_data)
        
        return volume_price_sum / total_volume if total_volume > 0 else 0.0


class AlgoExecutionManager:
    """
    Manager for algorithm executions
    """
    
    def __init__(self, client: LongPortClient, quote_processor: QuoteProcessor):
        self.client = client
        self.quote_processor = quote_processor
        self.active_executions = {}
        
    def create_twap(
        self,
        symbol: str,
        side: OrderSide,
        total_quantity: int,
        duration_seconds: int,
        num_slices: int = 10,
        price_limit: Optional[float] = None,
        price_offset_pct: float = 0.0
    ) -> str:
        """Create and start a TWAP execution"""
        exec_id = f"TWAP_{symbol}_{int(time.time())}"
        
        execution = TWAPExecution(
            self.client,
            self.quote_processor,
            symbol,
            side,
            total_quantity,
            duration_seconds,
            num_slices,
            price_limit,
            price_offset_pct,
            lambda status: self._on_execution_complete(exec_id, status)
        )
        
        # Start execution
        if execution.start():
            self.active_executions[exec_id] = execution
            logger.info(f"Started TWAP execution: {exec_id}")
            return exec_id
        
        return None
        
    def create_vwap(
        self,
        symbol: str,
        side: OrderSide,
        total_quantity: int,
        duration_seconds: int,
        historical_volume_profile: Optional[List[float]] = None,
        num_slices: int = 10,
        price_limit: Optional[float] = None,
        price_offset_pct: float = 0.0
    ) -> str:
        """Create and start a VWAP execution"""
        exec_id = f"VWAP_{symbol}_{int(time.time())}"
        
        execution = VWAPExecution(
            self.client,
            self.quote_processor,
            symbol,
            side,
            total_quantity,
            duration_seconds,
            historical_volume_profile,
            num_slices,
            price_limit,
            price_offset_pct,
            lambda status: self._on_execution_complete(exec_id, status)
        )
        
        # Start execution
        if execution.start():
            self.active_executions[exec_id] = execution
            logger.info(f"Started VWAP execution: {exec_id}")
            return exec_id
        
        return None
        
    def stop_execution(self, exec_id: str) -> bool:
        """Stop an active execution"""
        if exec_id in self.active_executions:
            self.active_executions[exec_id].stop()
            logger.info(f"Stopped execution: {exec_id}")
            return True
            
        return False
        
    def get_execution_status(self, exec_id: str = None) -> Dict[str, Any]:
        """Get status of executions"""
        if exec_id is not None:
            if exec_id in self.active_executions:
                return self.active_executions[exec_id].get_execution_status()
            return {}
            
        # Return status of all executions
        return {
            exec_id: execution.get_execution_status()
            for exec_id, execution in self.active_executions.items()
        }
        
    def _on_execution_complete(self, exec_id: str, status: Dict[str, Any]):
        """Handle execution completion"""
        logger.info(f"Execution completed: {exec_id}")
        
        # Remove from active executions after a delay
        def cleanup():
            time.sleep(60)  # Keep in active list for 1 minute after completion
            if exec_id in self.active_executions:
                self.active_executions.pop(exec_id, None)
                
        cleanup_thread = threading.Thread(target=cleanup)
        cleanup_thread.daemon = True
        cleanup_thread.start() 