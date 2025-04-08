#!/usr/bin/env python
# -*- coding: utf-8 -*-

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Union, Tuple
import logging
from sqlalchemy import desc, func, and_, or_

from databases.db import get_db_session
from databases.models import Stock, StockPrice, Signal, Order, ModelPerformance, SystemLog, SignalType, OrderStatus

logger = logging.getLogger("repository")

class StockRepository:
    """股票信息仓库"""
    
    @staticmethod
    def get_stock(symbol: str) -> Optional[Stock]:
        """根据股票代码获取股票信息"""
        with get_db_session() as session:
            return session.query(Stock).filter(Stock.symbol == symbol).first()
    
    @staticmethod
    def get_stock_by_id(stock_id: int) -> Optional[Stock]:
        """根据ID获取股票信息"""
        with get_db_session() as session:
            return session.query(Stock).filter(Stock.id == stock_id).first()
    
    @staticmethod
    def get_all_active_stocks() -> List[Stock]:
        """获取所有活跃股票"""
        with get_db_session() as session:
            return session.query(Stock).filter(Stock.is_active == True).all()
    
    @staticmethod
    def create_stock(symbol: str, name: str = None, exchange: str = None, sector: str = None, industry: str = None) -> Stock:
        """创建新的股票记录"""
        with get_db_session() as session:
            stock = Stock(
                symbol=symbol,
                name=name,
                exchange=exchange,
                sector=sector,
                industry=industry
            )
            session.add(stock)
            session.commit()
            session.refresh(stock)
            return stock
    
    @staticmethod
    def update_stock(stock_id: int, **kwargs) -> bool:
        """更新股票信息"""
        with get_db_session() as session:
            stock = session.query(Stock).filter(Stock.id == stock_id).first()
            if not stock:
                return False
                
            for key, value in kwargs.items():
                if hasattr(stock, key):
                    setattr(stock, key, value)
                    
            session.commit()
            return True

class StockPriceRepository:
    """股票价格仓库"""
    
    @staticmethod
    def add_price(stock_id: int, timestamp: datetime, open_price: float, high: float, low: float, close: float, volume: int, turnover: float) -> StockPrice:
        """添加股票价格记录"""
        with get_db_session() as session:
            price = StockPrice(
                stock_id=stock_id,
                timestamp=timestamp,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                turnover=turnover
            )
            session.add(price)
            session.commit()
            session.refresh(price)
            return price
    
    @staticmethod
    def get_latest_price(stock_id: int) -> Optional[StockPrice]:
        """获取最新价格"""
        with get_db_session() as session:
            return session.query(StockPrice).filter(StockPrice.stock_id == stock_id).order_by(desc(StockPrice.timestamp)).first()
    
    @staticmethod
    def get_prices(stock_id: int, start_time: datetime = None, end_time: datetime = None, limit: int = None) -> List[StockPrice]:
        """获取价格历史"""
        with get_db_session() as session:
            query = session.query(StockPrice).filter(StockPrice.stock_id == stock_id)
            
            if start_time:
                query = query.filter(StockPrice.timestamp >= start_time)
                
            if end_time:
                query = query.filter(StockPrice.timestamp <= end_time)
                
            query = query.order_by(desc(StockPrice.timestamp))
            
            if limit:
                query = query.limit(limit)
                
            return query.all()
    
    @staticmethod
    def get_daily_prices(stock_id: int, days: int = 30) -> List[StockPrice]:
        """获取每日收盘价"""
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
        
        return StockPriceRepository.get_prices(stock_id, start_time, end_time)
    
    @staticmethod
    def bulk_insert_prices(price_list: List[Dict[str, Any]]) -> int:
        """批量插入价格数据"""
        with get_db_session() as session:
            prices = []
            for price_data in price_list:
                price = StockPrice(**price_data)
                prices.append(price)
                
            session.bulk_save_objects(prices)
            session.commit()
            return len(prices)

class SignalRepository:
    """交易信号仓库"""
    
    @staticmethod
    def create_signal(
        stock_id: int, 
        signal_type: SignalType, 
        price: float, 
        confidence: float = 0.0,
        quantity: int = 0,
        timestamp: datetime = None,
        predicted_change_pct: float = None,
        predicted_price: float = None,
        extra_data: Dict[str, Any] = None
    ) -> Signal:
        """创建新的交易信号"""
        with get_db_session() as session:
            signal = Signal(
                stock_id=stock_id,
                signal_type=signal_type,
                price=price,
                confidence=confidence,
                quantity=quantity,
                timestamp=timestamp or datetime.now(),
                predicted_change_pct=predicted_change_pct,
                predicted_price=predicted_price,
                extra_data=extra_data
            )
            session.add(signal)
            session.commit()
            session.refresh(signal)
            return signal
    
    @staticmethod
    def get_signal(signal_id: int) -> Optional[Signal]:
        """根据ID获取信号"""
        with get_db_session() as session:
            return session.query(Signal).filter(Signal.id == signal_id).first()
    
    @staticmethod
    def get_latest_signal(stock_id: int) -> Optional[Signal]:
        """获取最新信号"""
        with get_db_session() as session:
            return session.query(Signal).filter(Signal.stock_id == stock_id).order_by(desc(Signal.timestamp)).first()
    
    @staticmethod
    def get_signals_by_type(signal_type: SignalType, start_time: datetime = None, end_time: datetime = None, limit: int = None) -> List[Signal]:
        """根据信号类型获取信号"""
        with get_db_session() as session:
            query = session.query(Signal).filter(Signal.signal_type == signal_type)
            
            if start_time:
                query = query.filter(Signal.timestamp >= start_time)
                
            if end_time:
                query = query.filter(Signal.timestamp <= end_time)
                
            query = query.order_by(desc(Signal.timestamp))
            
            if limit:
                query = query.limit(limit)
                
            return query.all()
    
    @staticmethod
    def get_signals_by_stock(stock_id: int, start_time: datetime = None, end_time: datetime = None, limit: int = None) -> List[Signal]:
        """获取指定股票的信号"""
        with get_db_session() as session:
            query = session.query(Signal).filter(Signal.stock_id == stock_id)
            
            if start_time:
                query = query.filter(Signal.timestamp >= start_time)
                
            if end_time:
                query = query.filter(Signal.timestamp <= end_time)
                
            query = query.order_by(desc(Signal.timestamp))
            
            if limit:
                query = query.limit(limit)
                
            return query.all()

class OrderRepository:
    """订单仓库"""
    
    @staticmethod
    def create_order(
        order_id: str,
        stock_id: int,
        side: str,
        quantity: int,
        price: float,
        signal_id: int = None,
        status: OrderStatus = OrderStatus.PENDING,
        submitted_at: datetime = None,
        executed_quantity: int = 0,
        executed_price: float = None,
        message: str = None
    ) -> Order:
        """创建新的订单"""
        with get_db_session() as session:
            order = Order(
                order_id=order_id,
                stock_id=stock_id,
                signal_id=signal_id,
                side=side,
                quantity=quantity,
                price=price,
                status=status,
                submitted_at=submitted_at or datetime.now(),
                executed_quantity=executed_quantity,
                executed_price=executed_price,
                message=message
            )
            session.add(order)
            session.commit()
            session.refresh(order)
            return order
    
    @staticmethod
    def update_order_status(order_id: str, status: OrderStatus, executed_quantity: int = None, executed_price: float = None, message: str = None) -> bool:
        """更新订单状态"""
        with get_db_session() as session:
            order = session.query(Order).filter(Order.order_id == order_id).first()
            if not order:
                return False
                
            order.status = status
            
            if executed_quantity is not None:
                order.executed_quantity = executed_quantity
                
            if executed_price is not None:
                order.executed_price = executed_price
                
            if message is not None:
                order.message = message
                
            session.commit()
            return True
    
    @staticmethod
    def get_order(order_id: str) -> Optional[Order]:
        """根据订单ID获取订单"""
        with get_db_session() as session:
            return session.query(Order).filter(Order.order_id == order_id).first()
    
    @staticmethod
    def get_orders_by_status(status: OrderStatus, limit: int = None) -> List[Order]:
        """根据状态获取订单"""
        with get_db_session() as session:
            query = session.query(Order).filter(Order.status == status)
            query = query.order_by(desc(Order.submitted_at))
            
            if limit:
                query = query.limit(limit)
                
            return query.all()
    
    @staticmethod
    def get_orders_by_stock(stock_id: int, start_time: datetime = None, end_time: datetime = None, limit: int = None) -> List[Order]:
        """获取指定股票的订单"""
        with get_db_session() as session:
            query = session.query(Order).filter(Order.stock_id == stock_id)
            
            if start_time:
                query = query.filter(Order.submitted_at >= start_time)
                
            if end_time:
                query = query.filter(Order.submitted_at <= end_time)
                
            query = query.order_by(desc(Order.submitted_at))
            
            if limit:
                query = query.limit(limit)
                
            return query.all()
    
    @staticmethod
    def get_today_orders() -> List[Order]:
        """获取今日订单"""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        
        with get_db_session() as session:
            return session.query(Order).filter(
                Order.submitted_at >= today,
                Order.submitted_at < tomorrow
            ).order_by(desc(Order.submitted_at)).all()
    
    @staticmethod
    def count_orders_by_status_today() -> Dict[OrderStatus, int]:
        """统计今日各状态订单数量"""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        
        with get_db_session() as session:
            result = session.query(
                Order.status, 
                func.count(Order.id)
            ).filter(
                Order.submitted_at >= today,
                Order.submitted_at < tomorrow
            ).group_by(Order.status).all()
            
            return {status: count for status, count in result}

class ModelPerformanceRepository:
    """模型性能仓库"""
    
    @staticmethod
    def save_performance(
        model_name: str,
        train_date: datetime,
        test_loss: float = None,
        validation_loss: float = None,
        prediction_accuracy: float = None,
        stock_id: int = None,
        parameters: Dict[str, Any] = None
    ) -> ModelPerformance:
        """保存模型性能记录"""
        with get_db_session() as session:
            performance = ModelPerformance(
                model_name=model_name,
                stock_id=stock_id,
                train_date=train_date,
                test_loss=test_loss,
                validation_loss=validation_loss,
                prediction_accuracy=prediction_accuracy,
                parameters=parameters
            )
            session.add(performance)
            session.commit()
            session.refresh(performance)
            return performance
    
    @staticmethod
    def get_latest_performance(model_name: str, stock_id: int = None) -> Optional[ModelPerformance]:
        """获取最新性能记录"""
        with get_db_session() as session:
            query = session.query(ModelPerformance).filter(ModelPerformance.model_name == model_name)
            
            if stock_id:
                query = query.filter(ModelPerformance.stock_id == stock_id)
                
            return query.order_by(desc(ModelPerformance.train_date)).first()
    
    @staticmethod
    def get_performance_history(model_name: str, stock_id: int = None, limit: int = None) -> List[ModelPerformance]:
        """获取性能历史"""
        with get_db_session() as session:
            query = session.query(ModelPerformance).filter(ModelPerformance.model_name == model_name)
            
            if stock_id:
                query = query.filter(ModelPerformance.stock_id == stock_id)
                
            query = query.order_by(desc(ModelPerformance.train_date))
            
            if limit:
                query = query.limit(limit)
                
            return query.all()

class SystemLogRepository:
    """系统日志仓库"""
    
    @staticmethod
    def log(level: str, component: str, message: str, details: Dict[str, Any] = None) -> SystemLog:
        """记录日志"""
        with get_db_session() as session:
            log = SystemLog(
                level=level.upper(),
                component=component,
                message=message,
                details=details,
                timestamp=datetime.now()
            )
            session.add(log)
            session.commit()
            session.refresh(log)
            return log
    
    @staticmethod
    def get_logs(
        level: str = None, 
        component: str = None, 
        start_time: datetime = None, 
        end_time: datetime = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[SystemLog]:
        """获取日志"""
        with get_db_session() as session:
            query = session.query(SystemLog)
            
            if level:
                query = query.filter(SystemLog.level == level.upper())
                
            if component:
                query = query.filter(SystemLog.component == component)
                
            if start_time:
                query = query.filter(SystemLog.timestamp >= start_time)
                
            if end_time:
                query = query.filter(SystemLog.timestamp <= end_time)
                
            query = query.order_by(desc(SystemLog.timestamp))
            
            if offset:
                query = query.offset(offset)
                
            if limit:
                query = query.limit(limit)
                
            return query.all()
    
    @staticmethod
    def get_errors(
        start_time: datetime = None, 
        end_time: datetime = None,
        limit: int = 100
    ) -> List[SystemLog]:
        """获取错误日志"""
        return SystemLogRepository.get_logs(
            level="ERROR",
            start_time=start_time,
            end_time=end_time,
            limit=limit
        ) 