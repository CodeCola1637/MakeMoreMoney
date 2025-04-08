#!/usr/bin/env python
# -*- coding: utf-8 -*-

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Enum, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

Base = declarative_base()

class SignalType(enum.Enum):
    """交易信号类型"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    UNKNOWN = "UNKNOWN"

class OrderStatus(enum.Enum):
    """订单状态"""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"

class Stock(Base):
    """股票信息表"""
    __tablename__ = 'stocks'
    
    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False, unique=True, index=True)
    name = Column(String(100))
    exchange = Column(String(20))
    sector = Column(String(50))
    industry = Column(String(50))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关系
    prices = relationship("StockPrice", back_populates="stock")
    signals = relationship("Signal", back_populates="stock")
    orders = relationship("Order", back_populates="stock")
    
    def __repr__(self):
        return f"<Stock(symbol='{self.symbol}', name='{self.name}')>"

class StockPrice(Base):
    """股票价格表"""
    __tablename__ = 'stock_prices'
    
    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stocks.id'), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)
    turnover = Column(Float)
    
    # 关系
    stock = relationship("Stock", back_populates="prices")
    
    def __repr__(self):
        return f"<StockPrice(symbol='{self.stock.symbol}', timestamp='{self.timestamp}', close='{self.close}')>"

class Signal(Base):
    """交易信号表"""
    __tablename__ = 'signals'
    
    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stocks.id'), nullable=False)
    signal_type = Column(Enum(SignalType), nullable=False)
    price = Column(Float, nullable=False)
    confidence = Column(Float, default=0.0)
    quantity = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.now)
    predicted_change_pct = Column(Float)
    predicted_price = Column(Float)
    extra_data = Column(JSON)
    
    # 关系
    stock = relationship("Stock", back_populates="signals")
    orders = relationship("Order", back_populates="signal")
    
    def __repr__(self):
        return f"<Signal(symbol='{self.stock.symbol}', type='{self.signal_type}', price='{self.price}')>"

class Order(Base):
    """订单表"""
    __tablename__ = 'orders'
    
    id = Column(Integer, primary_key=True)
    order_id = Column(String(50), nullable=False, unique=True, index=True)
    stock_id = Column(Integer, ForeignKey('stocks.id'), nullable=False)
    signal_id = Column(Integer, ForeignKey('signals.id'))
    side = Column(String(10), nullable=False)  # BUY/SELL
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    status = Column(Enum(OrderStatus), nullable=False, default=OrderStatus.PENDING)
    submitted_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    executed_quantity = Column(Integer, default=0)
    executed_price = Column(Float)
    message = Column(Text)
    
    # 关系
    stock = relationship("Stock", back_populates="orders")
    signal = relationship("Signal", back_populates="orders")
    
    def __repr__(self):
        return f"<Order(order_id='{self.order_id}', symbol='{self.stock.symbol}', status='{self.status}')>"

class ModelPerformance(Base):
    """模型性能表"""
    __tablename__ = 'model_performances'
    
    id = Column(Integer, primary_key=True)
    model_name = Column(String(100), nullable=False)
    stock_id = Column(Integer, ForeignKey('stocks.id'))
    train_date = Column(DateTime, nullable=False)
    test_loss = Column(Float)
    validation_loss = Column(Float)
    prediction_accuracy = Column(Float)
    parameters = Column(JSON)
    
    def __repr__(self):
        return f"<ModelPerformance(model='{self.model_name}', train_date='{self.train_date}')>"

class SystemLog(Base):
    """系统日志表"""
    __tablename__ = 'system_logs'
    
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now, index=True)
    level = Column(String(10), index=True)  # INFO, WARNING, ERROR, etc.
    component = Column(String(50), index=True)
    message = Column(Text, nullable=False)
    details = Column(JSON)
    
    def __repr__(self):
        return f"<SystemLog(timestamp='{self.timestamp}', level='{self.level}', message='{self.message[:50]}...')>" 