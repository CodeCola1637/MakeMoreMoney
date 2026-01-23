"""
智能挂单管理器
负责管理、监控和优化挂单，包括过期订单清理、价格偏离检查、信号冲突检测等
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

from utils import setup_logger


class CancelReason(Enum):
    """取消原因"""
    EXPIRED = "订单过期"
    PRICE_DEVIATION = "价格偏离过大"
    SIGNAL_CONFLICT = "信号冲突"
    MANUAL = "手动取消"
    MARKET_CLOSED = "市场关闭"
    INSUFFICIENT_FUNDS = "资金不足"
    POSITION_LIMIT = "仓位限制"


@dataclass
class PendingOrderInfo:
    """挂单信息"""
    order_id: str
    symbol: str
    side: str  # 'Buy' or 'Sell'
    price: float
    quantity: int
    submitted_at: datetime
    strategy_name: str = ""
    last_check: Optional[datetime] = None
    check_count: int = 0
    price_deviation_history: List[float] = field(default_factory=list)


class PendingOrderManager:
    """
    智能挂单管理器
    
    功能：
    1. 监控挂单状态
    2. 自动清理过期挂单
    3. 检测价格偏离并决定是否取消
    4. 检测信号冲突（如收到反向信号）
    5. 挂单统计和分析
    """
    
    def __init__(self, order_manager, config, realtime_mgr=None, logger=None):
        """
        初始化挂单管理器
        
        Args:
            order_manager: 订单管理器实例
            config: 配置加载器
            realtime_mgr: 实时数据管理器（可选，用于获取当前价格）
            logger: 日志记录器
        """
        self.order_manager = order_manager
        self.config = config
        self.realtime_mgr = realtime_mgr
        
        self.logger = logger or setup_logger(
            "pending_order_manager",
            config.get("logging.level", "INFO"),
            config.get("logging.file")
        )
        
        # 配置参数
        self.max_pending_hours = config.get("execution.order_tracking.max_pending_age", 4)
        self.max_pending_age = timedelta(hours=self.max_pending_hours)
        self.price_deviation_threshold = config.get("execution.order_tracking.price_deviation_threshold", 0.02)
        self.check_interval = config.get("execution.order_tracking.check_interval", 60)  # 秒
        
        # 挂单缓存
        self.pending_orders: Dict[str, PendingOrderInfo] = {}
        
        # 统计信息
        self.stats = {
            'total_cancelled': 0,
            'cancelled_by_reason': {reason.value: 0 for reason in CancelReason},
            'total_filled': 0,
            'total_checked': 0,
            'last_cleanup_time': None
        }
        
        # 最新信号缓存（用于检测信号冲突）
        self.latest_signals: Dict[str, Dict] = {}  # {symbol: {'type': 'BUY'/'SELL', 'timestamp': datetime}}
        
        self.logger.info(f"✅ 挂单管理器初始化完成 - 最大挂单时间: {self.max_pending_hours}小时, "
                        f"价格偏离阈值: {self.price_deviation_threshold:.1%}")
    
    async def refresh_pending_orders(self):
        """刷新挂单列表"""
        try:
            pending_list = await self.order_manager.get_pending_orders()
            
            # 更新挂单缓存
            current_ids = set()
            for order in pending_list:
                order_id = str(order.order_id)
                current_ids.add(order_id)
                
                if order_id not in self.pending_orders:
                    # 新挂单
                    self.pending_orders[order_id] = PendingOrderInfo(
                        order_id=order_id,
                        symbol=order.symbol,
                        side=str(order.side),
                        price=float(order.price),
                        quantity=int(order.quantity),
                        submitted_at=getattr(order, 'submitted_at', datetime.now()),
                        strategy_name=getattr(order, 'strategy_name', '')
                    )
            
            # 移除已不在挂单列表中的订单（已成交或已取消）
            removed_ids = set(self.pending_orders.keys()) - current_ids
            for order_id in removed_ids:
                del self.pending_orders[order_id]
            
            self.logger.debug(f"刷新挂单列表: 当前{len(self.pending_orders)}个挂单")
            
        except Exception as e:
            self.logger.error(f"刷新挂单列表失败: {e}")
    
    async def cleanup_stale_orders(self) -> List[Tuple[str, CancelReason]]:
        """
        清理过期和无效的挂单
        
        Returns:
            已取消订单列表 [(order_id, cancel_reason), ...]
        """
        cancelled_orders = []
        
        try:
            await self.refresh_pending_orders()
            
            for order_id, order_info in list(self.pending_orders.items()):
                should_cancel, reason = await self._should_cancel_order(order_info)
                
                if should_cancel:
                    try:
                        success = await self.order_manager.cancel_order(order_id)
                        if success:
                            cancelled_orders.append((order_id, reason))
                            self.stats['total_cancelled'] += 1
                            self.stats['cancelled_by_reason'][reason.value] += 1
                            self.logger.info(f"✅ 取消挂单: {order_id} ({order_info.symbol}), 原因: {reason.value}")
                        else:
                            self.logger.warning(f"取消挂单失败: {order_id}")
                    except Exception as e:
                        self.logger.error(f"取消挂单异常: {order_id}, 错误: {e}")
                
                # 更新检查信息
                order_info.last_check = datetime.now()
                order_info.check_count += 1
                self.stats['total_checked'] += 1
            
            self.stats['last_cleanup_time'] = datetime.now()
            
            if cancelled_orders:
                self.logger.info(f"📋 清理完成: 取消了 {len(cancelled_orders)} 个挂单")
            
        except Exception as e:
            self.logger.error(f"清理挂单失败: {e}")
        
        return cancelled_orders
    
    async def _should_cancel_order(self, order_info: PendingOrderInfo) -> Tuple[bool, Optional[CancelReason]]:
        """
        判断是否应该取消订单
        
        Args:
            order_info: 挂单信息
            
        Returns:
            (是否取消, 取消原因)
        """
        now = datetime.now()
        
        # 1. 检查订单年龄
        order_age = now - order_info.submitted_at
        if order_age > self.max_pending_age:
            return True, CancelReason.EXPIRED
        
        # 2. 检查价格偏离
        current_price = await self._get_current_price(order_info.symbol)
        if current_price:
            deviation = abs(order_info.price - current_price) / current_price
            order_info.price_deviation_history.append(deviation)
            
            # 保持最近10次偏离记录
            if len(order_info.price_deviation_history) > 10:
                order_info.price_deviation_history = order_info.price_deviation_history[-10:]
            
            if deviation > self.price_deviation_threshold:
                return True, CancelReason.PRICE_DEVIATION
        
        # 3. 检查信号冲突
        if order_info.symbol in self.latest_signals:
            latest_signal = self.latest_signals[order_info.symbol]
            signal_type = latest_signal.get('type', '')
            signal_time = latest_signal.get('timestamp', datetime.min)
            
            # 如果最新信号在订单提交之后且方向相反
            if signal_time > order_info.submitted_at:
                if (order_info.side == 'Buy' and signal_type == 'SELL') or \
                   (order_info.side == 'Sell' and signal_type == 'BUY'):
                    return True, CancelReason.SIGNAL_CONFLICT
        
        # 4. 检查市场状态（简化版）
        # 可以扩展为检查具体市场的交易时间
        
        return False, None
    
    async def _get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前价格"""
        try:
            if self.realtime_mgr:
                quotes = await self.realtime_mgr.get_quote([symbol])
                if quotes and symbol in quotes:
                    return float(quotes[symbol].last_done)
        except Exception as e:
            self.logger.debug(f"获取 {symbol} 价格失败: {e}")
        return None
    
    def update_latest_signal(self, symbol: str, signal_type: str):
        """
        更新最新信号（供外部调用）
        
        Args:
            symbol: 股票代码
            signal_type: 信号类型 ('BUY' or 'SELL')
        """
        self.latest_signals[symbol] = {
            'type': signal_type.upper(),
            'timestamp': datetime.now()
        }
    
    async def analyze_pending_efficiency(self) -> Dict[str, Any]:
        """
        分析挂单效率
        
        Returns:
            效率分析报告
        """
        await self.refresh_pending_orders()
        
        analysis = {
            'total_pending': len(self.pending_orders),
            'pending_by_symbol': {},
            'pending_by_side': {'Buy': 0, 'Sell': 0},
            'average_pending_time': 0,
            'price_deviation_stats': {
                'average': 0,
                'max': 0,
                'min': float('inf')
            },
            'oldest_order': None,
            'recommendations': []
        }
        
        if not self.pending_orders:
            return analysis
        
        total_pending_time = timedelta()
        all_deviations = []
        
        for order_info in self.pending_orders.values():
            # 按股票统计
            symbol = order_info.symbol
            if symbol not in analysis['pending_by_symbol']:
                analysis['pending_by_symbol'][symbol] = 0
            analysis['pending_by_symbol'][symbol] += 1
            
            # 按方向统计
            side = 'Buy' if 'Buy' in order_info.side else 'Sell'
            analysis['pending_by_side'][side] += 1
            
            # 计算挂单时间
            pending_time = datetime.now() - order_info.submitted_at
            total_pending_time += pending_time
            
            # 记录最老订单
            if analysis['oldest_order'] is None or order_info.submitted_at < analysis['oldest_order']['submitted_at']:
                analysis['oldest_order'] = {
                    'order_id': order_info.order_id,
                    'symbol': order_info.symbol,
                    'submitted_at': order_info.submitted_at,
                    'pending_hours': pending_time.total_seconds() / 3600
                }
            
            # 收集价格偏离数据
            if order_info.price_deviation_history:
                all_deviations.extend(order_info.price_deviation_history)
        
        # 计算平均挂单时间
        analysis['average_pending_time'] = (total_pending_time / len(self.pending_orders)).total_seconds() / 3600
        
        # 计算价格偏离统计
        if all_deviations:
            import numpy as np
            analysis['price_deviation_stats'] = {
                'average': float(np.mean(all_deviations)),
                'max': float(np.max(all_deviations)),
                'min': float(np.min(all_deviations))
            }
        
        # 生成建议
        if analysis['average_pending_time'] > 2:
            analysis['recommendations'].append("平均挂单时间较长，建议检查限价单策略")
        
        if analysis['price_deviation_stats']['average'] > 0.01:
            analysis['recommendations'].append("价格偏离较大，建议考虑使用市价单或调整限价策略")
        
        buy_count = analysis['pending_by_side']['Buy']
        sell_count = analysis['pending_by_side']['Sell']
        if buy_count > 0 and sell_count > 0 and abs(buy_count - sell_count) > 5:
            analysis['recommendations'].append(f"买卖挂单不均衡 (买{buy_count}:卖{sell_count})，建议检查策略")
        
        return analysis
    
    def get_summary(self) -> Dict[str, Any]:
        """获取挂单管理器状态摘要"""
        return {
            'pending_count': len(self.pending_orders),
            'stats': self.stats.copy(),
            'config': {
                'max_pending_hours': self.max_pending_hours,
                'price_deviation_threshold': self.price_deviation_threshold,
                'check_interval': self.check_interval
            }
        }
    
    def get_pending_orders_list(self) -> List[Dict]:
        """获取当前挂单列表"""
        return [
            {
                'order_id': info.order_id,
                'symbol': info.symbol,
                'side': info.side,
                'price': info.price,
                'quantity': info.quantity,
                'submitted_at': info.submitted_at.isoformat(),
                'pending_hours': (datetime.now() - info.submitted_at).total_seconds() / 3600,
                'check_count': info.check_count
            }
            for info in self.pending_orders.values()
        ]


def create_pending_order_manager(order_manager, config, realtime_mgr=None, logger=None) -> PendingOrderManager:
    """
    工厂函数：创建挂单管理器
    
    Args:
        order_manager: 订单管理器实例
        config: 配置加载器
        realtime_mgr: 实时数据管理器
        logger: 日志记录器
        
    Returns:
        PendingOrderManager 实例
    """
    return PendingOrderManager(order_manager, config, realtime_mgr, logger)
