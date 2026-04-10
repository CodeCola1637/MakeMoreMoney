"""
信号过滤器
防止重复信号和过度交易，降低无效交易次数
"""

import asyncio
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Tuple, Optional, List, Dict
from strategy.signals import Signal, SignalType


class SignalFilter:
    """
    信号过滤器 - 防止重复信号和过度交易
    
    功能：
    1. 信号冷却期检查 - 同一股票在冷却期内不重复发送相同类型信号
    2. 每日信号数量限制 - 限制每个股票每日最大信号数量
    3. 价格变化阈值 - 只有价格变化超过阈值才生成新信号
    4. 置信度过滤 - 过滤低置信度信号
    5. 重复信号检测 - 防止短时间内发送相同信号
    """
    
    def __init__(self, config, logger=None):
        """
        初始化信号过滤器
        
        Args:
            config: 配置对象
            logger: 日志记录器
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # 配置参数
        self.cooldown_seconds = config.get("strategy.signal_cooldown", 600)
        self.max_signals_per_day = config.get("strategy.max_signals_per_day", 10)
        self.price_change_threshold = config.get("strategy.price_change_threshold", 0.01)
        self.min_confidence = config.get("strategy.signal_processing.confidence_threshold", 0.15)
        self.min_holding_seconds = config.get("strategy.min_holding_seconds", 300)
        
        # 止损后再入场冷却期（防止 止损→买入→止损 循环）
        self._stop_loss_reentry_cooldown = config.get("strategy.stop_loss_reentry_cooldown", 1800)
        self._stop_loss_exits: Dict[str, datetime] = {}
        
        # 并发保护锁
        self._lock = asyncio.Lock()
        
        # 信号历史记录
        self.signal_history: Dict[str, List[Tuple[datetime, str, float]]] = defaultdict(list)
        # {symbol: [(timestamp, signal_type, price), ...]}
        
        # 上次信号价格
        self.last_signal_price: Dict[str, float] = {}
        
        # 过滤统计
        self.filter_stats = defaultdict(lambda: defaultdict(int))
        # {symbol: {reason: count}}
        
        self.logger.info(f"信号过滤器初始化完成 - 冷却期: {self.cooldown_seconds}秒, "
                        f"每日上限: {self.max_signals_per_day}, "
                        f"价格阈值: {self.price_change_threshold:.1%}, "
                        f"最低置信度: {self.min_confidence}")
    
    async def should_emit_signal(self, signal: Signal) -> Tuple[bool, str]:
        """
        判断是否应该发出信号（async，内部加锁防止并发竞态）
        
        Args:
            signal: 交易信号对象
            
        Returns:
            Tuple[bool, str]: (是否应该发出, 原因说明)
        """
        # HOLD 信号无需加锁，直接跳过
        if signal.signal_type == SignalType.HOLD:
            return False, "HOLD信号，不执行交易"
        
        async with self._lock:
            symbol = signal.symbol
            now = datetime.now()
            signal_type_str = signal.signal_type.value if hasattr(signal.signal_type, 'value') else str(signal.signal_type)
            
            # 1. 检查置信度
            confidence = getattr(signal, 'confidence', 1.0)
            if confidence < self.min_confidence:
                self._record_filter(symbol, "confidence_too_low")
                return False, f"置信度不足: {confidence:.3f} < {self.min_confidence}"
            
            # 2. 止损后再入场冷却
            if signal.signal_type == SignalType.BUY and symbol in self._stop_loss_exits:
                last_exit = self._stop_loss_exits[symbol]
                elapsed = (now - last_exit).total_seconds()
                if elapsed < self._stop_loss_reentry_cooldown:
                    remaining = int(self._stop_loss_reentry_cooldown - elapsed)
                    self._record_filter(symbol, "stop_loss_reentry_cooldown")
                    return False, f"止损后冷却期: {symbol} 在 {int(elapsed)}秒前止损卖出, 还需等待{remaining}秒才能重新买入"
            
            # 3. 检查冷却时间
            cooldown = self.cooldown_seconds
            if symbol in self.signal_history:
                recent_signals = [
                    (ts, st, price) for ts, st, price in self.signal_history[symbol]
                    if now - ts < timedelta(seconds=cooldown)
                ]
                
                if recent_signals:
                    last_ts, last_type, last_price = recent_signals[-1]
                    if last_type == signal_type_str:
                        elapsed = (now - last_ts).seconds
                        remaining = cooldown - elapsed
                        self._record_filter(symbol, "cooldown_period")
                        return False, f"冷却期内: 距上次{last_type}信号仅{elapsed}秒, 还需等待{remaining}秒 (冷却期={cooldown}s)"
            
            # 3.5 最小持仓时间 — 防止秒级买卖
            if symbol in self.signal_history and self.signal_history[symbol]:
                last_ts, last_type, _ = self.signal_history[symbol][-1]
                is_reversal = (
                    (last_type == "BUY" and signal_type_str == "SELL") or
                    (last_type == "SELL" and signal_type_str == "BUY")
                )
                if is_reversal:
                    elapsed = (now - last_ts).total_seconds()
                    if elapsed < self.min_holding_seconds:
                        remaining = int(self.min_holding_seconds - elapsed)
                        self._record_filter(symbol, "min_holding_time")
                        return False, (
                            f"最小持仓时间未到: {last_type}→{signal_type_str} "
                            f"仅{int(elapsed)}秒, 需等待{remaining}秒"
                        )

            # 4. 检查每日信号数量限制
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_signals = [
                (ts, st, price) for ts, st, price in self.signal_history.get(symbol, [])
                if ts >= today_start
            ]
            if len(today_signals) >= self.max_signals_per_day:
                self._record_filter(symbol, "daily_limit_reached")
                return False, f"已达到每日信号上限: {len(today_signals)}/{self.max_signals_per_day}"
            
            # 5. 检查价格变化阈值
            if symbol in self.last_signal_price and self.last_signal_price[symbol] > 0:
                last_price = self.last_signal_price[symbol]
                current_price = signal.price
                if current_price > 0:
                    price_change = abs(current_price - last_price) / last_price
                    if price_change < self.price_change_threshold:
                        self._record_filter(symbol, "price_change_insufficient")
                        return False, f"价格变化不足: {price_change:.2%} < {self.price_change_threshold:.2%}"
            
            # 6. 记录方向反转（仅日志，不阻止）
            if symbol in self.signal_history and self.signal_history[symbol]:
                last_signal_type = self.signal_history[symbol][-1][1]
                if last_signal_type != signal_type_str:
                    self.logger.info(f"信号方向反转: {symbol} {last_signal_type} -> {signal_type_str}")
            
            return True, "通过所有过滤条件"
    
    async def record_signal(self, signal: Signal):
        """
        记录已发出的信号（async，内部加锁）
        
        Args:
            signal: 已发出的信号对象
        """
        async with self._lock:
            symbol = signal.symbol
            signal_type_str = signal.signal_type.value if hasattr(signal.signal_type, 'value') else str(signal.signal_type)
            
            self.signal_history[symbol].append((datetime.now(), signal_type_str, signal.price))
            self.last_signal_price[symbol] = signal.price
            
            # 清理过期历史（保留最近24小时）
            self._cleanup_history(symbol)
            
            self.logger.debug(f"记录信号: {symbol} {signal_type_str} @ {signal.price}")
    
    def _cleanup_history(self, symbol: str):
        """清理过期的信号历史"""
        cutoff = datetime.now() - timedelta(days=1)
        self.signal_history[symbol] = [
            (ts, st, price) for ts, st, price in self.signal_history[symbol]
            if ts > cutoff
        ]
    
    async def record_stop_loss_exit(self, symbol: str):
        """记录止损卖出事件，触发再入场冷却期（async，内部加锁）"""
        async with self._lock:
            self._stop_loss_exits[symbol] = datetime.now()
            self.logger.info(f"记录止损退出: {symbol}, 再入场冷却 {self._stop_loss_reentry_cooldown}秒")
    
    def _record_filter(self, symbol: str, reason: str):
        """记录过滤统计"""
        self.filter_stats[symbol][reason] += 1
    
    def get_filter_stats(self, symbol: Optional[str] = None) -> Dict:
        """
        获取过滤统计信息
        
        Args:
            symbol: 可选，指定股票代码；如果为None则返回所有统计
            
        Returns:
            过滤统计字典
        """
        if symbol:
            return dict(self.filter_stats.get(symbol, {}))
        return {s: dict(stats) for s, stats in self.filter_stats.items()}
    
    def get_signal_count_today(self, symbol: str) -> int:
        """
        获取今日信号数量
        
        Args:
            symbol: 股票代码
            
        Returns:
            今日信号数量
        """
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_signals = [
            (ts, st, price) for ts, st, price in self.signal_history.get(symbol, [])
            if ts >= today_start
        ]
        return len(today_signals)
    
    def get_remaining_cooldown(self, symbol: str) -> int:
        """
        获取剩余冷却时间（秒）
        
        Args:
            symbol: 股票代码
            
        Returns:
            剩余冷却时间（秒），如果不在冷却期返回0
        """
        if symbol not in self.signal_history or not self.signal_history[symbol]:
            return 0
        
        now = datetime.now()
        last_ts = self.signal_history[symbol][-1][0]
        elapsed = (now - last_ts).total_seconds()
        
        if elapsed >= self.cooldown_seconds:
            return 0
        
        return int(self.cooldown_seconds - elapsed)
    
    def reset_symbol_history(self, symbol: str):
        """
        重置指定股票的信号历史
        
        Args:
            symbol: 股票代码
        """
        if symbol in self.signal_history:
            del self.signal_history[symbol]
        if symbol in self.last_signal_price:
            del self.last_signal_price[symbol]
        if symbol in self.filter_stats:
            del self.filter_stats[symbol]
        
        self.logger.info(f"已重置 {symbol} 的信号历史")
    
    def reset_all_history(self):
        """重置所有信号历史"""
        self.signal_history.clear()
        self.last_signal_price.clear()
        self.filter_stats.clear()
        self.logger.info("已重置所有信号历史")
    
    def get_summary(self) -> str:
        """
        获取过滤器状态摘要
        
        Returns:
            格式化的状态摘要
        """
        lines = ["📊 信号过滤器状态摘要:"]
        lines.append(f"   冷却期: {self.cooldown_seconds}秒")
        lines.append(f"   每日上限: {self.max_signals_per_day}")
        lines.append(f"   价格阈值: {self.price_change_threshold:.1%}")
        lines.append(f"   最低置信度: {self.min_confidence}")
        
        if self.signal_history:
            lines.append("   股票信号统计:")
            for symbol, signals in self.signal_history.items():
                today_count = self.get_signal_count_today(symbol)
                cooldown = self.get_remaining_cooldown(symbol)
                lines.append(f"     - {symbol}: 今日{today_count}次, 冷却剩余{cooldown}秒")
        
        if self.filter_stats:
            lines.append("   过滤统计:")
            for symbol, stats in self.filter_stats.items():
                for reason, count in stats.items():
                    lines.append(f"     - {symbol}: {reason} x {count}")
        
        return "\n".join(lines)
