import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime

from data_loader.realtime import RealtimeDataManager
from execution.order_manager import OrderManager
from strategy.signals import SignalType, Signal

logger = logging.getLogger(__name__)

class TradeExecutor:
    """交易执行器，负责执行交易信号"""
    
    def __init__(self, config: Dict, realtime_mgr: RealtimeDataManager):
        """初始化交易执行器
        
        Args:
            config: 配置信息
            realtime_mgr: 实时数据管理器
        """
        self.config = config
        self.realtime_mgr = realtime_mgr
        self.order_manager = OrderManager(config)
        self.running = False
        self.signal_check_interval = config.get("execution.signal_check_interval", 1)  # 默认1秒检查一次
        
    async def start(self):
        """启动交易执行器"""
        if self.running:
            logger.warning("TradeExecutor is already running")
            return
            
        self.running = True
        logger.info("Starting TradeExecutor...")
        
        try:
            # 初始化订单管理器
            await self.order_manager.initialize()
            
            # 主循环
            while self.running:
                try:
                    # 获取最新的交易信号
                    signals = self.realtime_mgr.get_latest_signals()
                    
                    # 处理每个信号
                    for symbol, signal_data in signals.items():
                        if signal_data and "quote" in signal_data:
                            quote = signal_data["quote"]
                            
                            # 创建信号对象
                            signal = Signal(
                                symbol=symbol,
                                signal_type=self._determine_signal_type(quote),
                                price=quote.get("last_done", 0),
                                confidence=0.8,  # 默认置信度
                                quantity=self._calculate_position_size(quote),
                                extra_data=quote
                            )
                            
                            # 处理信号
                            await self._process_signal(symbol, signal)
                        
                    # 等待下一次检查
                    await asyncio.sleep(self.signal_check_interval)
                    
                except Exception as e:
                    logger.error(f"Error in TradeExecutor main loop: {e}")
                    await asyncio.sleep(5)  # 发生错误时等待更长时间
                    
        except Exception as e:
            logger.error(f"Error starting TradeExecutor: {e}")
            self.running = False
            raise
            
    def _determine_signal_type(self, quote: Dict) -> SignalType:
        """根据行情数据确定信号类型"""
        try:
            # 这里可以实现更复杂的信号生成逻辑
            # 当前简单实现：如果最新价格高于开盘价，生成买入信号；否则生成卖出信号
            last_done = quote.get("last_done", 0)
            open_price = quote.get("open", 0)
            
            if last_done > open_price:
                return SignalType.BUY
            elif last_done < open_price:
                return SignalType.SELL
            else:
                return SignalType.HOLD
                
        except Exception as e:
            logger.error(f"确定信号类型时出错: {e}")
            return SignalType.UNKNOWN
            
    def _calculate_position_size(self, quote: Dict) -> int:
        """计算建议持仓数量"""
        try:
            # 这里可以实现更复杂的仓位计算逻辑
            # 当前简单实现：固定交易100股
            return 100
            
        except Exception as e:
            logger.error(f"计算仓位大小时出错: {e}")
            return 0
            
    async def stop(self):
        """停止交易执行器"""
        if not self.running:
            return
            
        logger.info("Stopping TradeExecutor...")
        self.running = False
        
        try:
            await self.order_manager.close()
        except Exception as e:
            logger.error(f"Error closing OrderManager: {e}")
            
    async def _process_signal(self, symbol: str, signal: Signal):
        """处理交易信号
        
        Args:
            symbol: 股票代码
            signal: 交易信号
        """
        try:
            # 获取当前持仓
            position = await self.order_manager.get_position(symbol)
            
            # 根据信号类型执行交易
            if signal.signal_type == SignalType.BUY and (position is None or position.quantity == 0):
                # 执行买入
                await self.order_manager.place_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=signal.quantity,
                    price_type="MARKET"
                )
                logger.info(f"下单买入: {symbol}, 数量: {signal.quantity}")
                
            elif signal.signal_type == SignalType.SELL and position is not None and position.quantity > 0:
                # 执行卖出
                await self.order_manager.place_order(
                    symbol=symbol,
                    side="SELL",
                    quantity=position.quantity,  # 卖出全部持仓
                    price_type="MARKET"
                )
                logger.info(f"下单卖出: {symbol}, 数量: {position.quantity}")
                
        except Exception as e:
            logger.error(f"Error processing signal for {symbol}: {e}") 