"""
止盈止损管理器
负责监控持仓的盈亏状况，并在适当时机触发止盈止损操作
"""

import asyncio
import logging
import pytz
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from decimal import Decimal


@dataclass
class PositionStatus:
    """持仓状态"""
    symbol: str
    quantity: int
    cost_price: float           # 成本价
    current_price: float        # 当前价格
    unrealized_pnl: float       # 未实现盈亏
    unrealized_pnl_pct: float   # 未实现盈亏百分比
    highest_price: float        # 持仓期间最高价
    trailing_stop_price: float  # 追踪止损价格
    last_update: datetime       # 最后更新时间


@dataclass 
class ExitSignal:
    """退出信号"""
    symbol: str
    signal_type: str    # TAKE_PROFIT, STOP_LOSS, TRAILING_STOP
    quantity: int       # 卖出数量
    price: float        # 建议卖出价格
    reason: str         # 退出原因
    urgency: int        # 紧急程度 (1-10)


class ProfitStopManager:
    """止盈止损管理器"""
    
    def __init__(self, config, order_manager, logger=None):
        self.config = config
        self.order_manager = order_manager
        self.logger = logger or logging.getLogger(__name__)
        
        # 配置参数
        self.profit_config = config.get('execution.profit_taking', {})
        self.stop_config = config.get('execution.stop_loss', {})
        
        # 启用标志
        self.profit_enabled = self.profit_config.get('enable', True)
        self.stop_enabled = self.stop_config.get('enable', True)
        
        # 止盈配置
        self.fixed_profit_pct = self.profit_config.get('fixed_profit_pct', 15.0)
        self.partial_profit_pct = self.profit_config.get('partial_profit_pct', 8.0)
        self.trailing_profit_pct = self.profit_config.get('trailing_profit_pct', 5.0)
        self.trailing_profit_step = self.profit_config.get('trailing_profit_step', 1.0)
        
        # 止损配置
        self.fixed_stop_pct = self.stop_config.get('fixed_stop_pct', 8.0)
        self.trailing_stop_pct = self.stop_config.get('trailing_stop_pct', 3.0)
        self.max_loss_per_day = self.stop_config.get('max_loss_per_day', 5.0)
        self.emergency_stop_pct = self.stop_config.get('emergency_stop_pct', 15.0)
        
        # 杠杆产品特殊阈值
        self._leveraged_symbols = set(config.get('execution.leveraged_symbols', []))
        self._leveraged_overrides = config.get('execution.leveraged_overrides', {})
        if self._leveraged_symbols:
            self.logger.info(f"杠杆产品特殊阈值已加载: {self._leveraged_symbols}, 覆盖: {self._leveraged_overrides}")
        
        # 持仓状态追踪
        self.position_status: Dict[str, PositionStatus] = {}
        self.daily_pnl = 0.0
        self.daily_pnl_reset_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # exit_pending tracks symbols with active exit attempts to prevent infinite retries
        # {symbol: {"last_attempt": datetime, "retry_count": int, "signal_type": str}}
        self._exit_pending: Dict[str, dict] = {}
        self._exit_retry_cooldown = timedelta(minutes=5)
        self._exit_max_retries = 5
        
        self.logger.info(f"止盈止损管理器初始化完成 - 止盈启用: {self.profit_enabled}, 止损启用: {self.stop_enabled}")
    
    def get_real_cost_price(self, symbol: str) -> Optional[float]:
        """
        从持仓信息获取真实成本价
        
        Args:
            symbol: 股票代码
            
        Returns:
            真实成本价，如果获取失败则返回None
        """
        try:
            # 从order_manager获取持仓列表
            positions = self.order_manager.get_positions(symbol)
            
            if not positions:
                self.logger.debug(f"未找到 {symbol} 的持仓信息")
                return None
            
            # 查找匹配的持仓
            for pos in positions:
                pos_symbol = getattr(pos, 'symbol', '').upper()
                if pos_symbol == symbol.upper():
                    # 长桥API的持仓对象有cost_price属性
                    cost_price = getattr(pos, 'cost_price', None)
                    if cost_price is not None:
                        cost_price_float = float(cost_price)
                        self.logger.debug(f"获取到 {symbol} 真实成本价: {cost_price_float}")
                        return cost_price_float
                    else:
                        self.logger.warning(f"{symbol} 持仓信息中无成本价属性")
            
            return None
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} 成本价失败: {e}")
            return None
    
    async def get_real_cost_price_async(self, symbol: str) -> Optional[float]:
        """
        异步版本：从持仓信息获取真实成本价
        
        Args:
            symbol: 股票代码
            
        Returns:
            真实成本价，如果获取失败则返回None
        """
        return self.get_real_cost_price(symbol)
        
    async def update_position_status(self, symbol: str, quantity: int, cost_price: float, current_price: float):
        """更新持仓状态"""
        try:
            # 计算盈亏（自动处理多空方向）
            # 多头：价格上涨盈利
            # 空头：价格下跌盈利（quantity为负数时自动反转）
            unrealized_pnl = (current_price - cost_price) * quantity
            
            # 计算盈亏百分比（空头仓位需要反转符号）
            is_short = quantity < 0
            if is_short:
                # 空头：价格下跌是盈利，百分比需要取反
                unrealized_pnl_pct = -((current_price - cost_price) / cost_price) * 100
            else:
                # 多头：价格上涨是盈利
                unrealized_pnl_pct = ((current_price - cost_price) / cost_price) * 100
            
            # 更新或创建持仓状态
            t = self._get_thresholds(symbol)
            trailing_pct = t['trailing_stop_pct']

            if symbol not in self.position_status:
                self.position_status[symbol] = PositionStatus(
                    symbol=symbol,
                    quantity=quantity,
                    cost_price=float(cost_price),
                    current_price=float(current_price),
                    unrealized_pnl=float(unrealized_pnl),
                    unrealized_pnl_pct=float(unrealized_pnl_pct),
                    highest_price=float(current_price),
                    trailing_stop_price=float(cost_price) * (1 - trailing_pct / 100),
                    last_update=datetime.now()
                )
                self.logger.info(f"创建持仓状态跟踪: {symbol}, 成本价: {cost_price}, 当前价: {current_price}, 追踪止损%: {trailing_pct}")
            else:
                status = self.position_status[symbol]
                status.quantity = quantity
                status.current_price = float(current_price)
                status.unrealized_pnl = float(unrealized_pnl)
                status.unrealized_pnl_pct = float(unrealized_pnl_pct)
                status.last_update = datetime.now()
                
                is_short = quantity < 0
                if is_short:
                    if float(current_price) < status.highest_price:
                        status.highest_price = float(current_price)
                        new_trailing_stop = float(current_price) * (1 + trailing_pct / 100)
                        if new_trailing_stop < status.trailing_stop_price:
                            status.trailing_stop_price = new_trailing_stop
                            self.logger.debug(f"更新空头追踪止损价格: {symbol}, 新止损价: {new_trailing_stop:.2f}")
                else:
                    if float(current_price) > status.highest_price:
                        status.highest_price = float(current_price)
                        new_trailing_stop = float(current_price) * (1 - trailing_pct / 100)
                        if new_trailing_stop > status.trailing_stop_price:
                            status.trailing_stop_price = new_trailing_stop
                            self.logger.debug(f"更新多头追踪止损价格: {symbol}, 新止损价: {new_trailing_stop:.2f}")
                
                self.logger.debug(f"更新持仓状态: {symbol}, 盈亏: {unrealized_pnl_pct:.2f}%, 最高价: {status.highest_price:.2f}")
                
        except Exception as e:
            self.logger.error(f"更新持仓状态失败: {symbol}, 错误: {e}")
    
    def _is_market_open(self, symbol: str) -> bool:
        """Check if the market for the given symbol is currently open."""
        try:
            if symbol.endswith('.HK'):
                hk_tz = pytz.timezone('Asia/Hong_Kong')
                now = datetime.now(hk_tz)
                if now.weekday() >= 5:
                    return False
                t = now.hour * 60 + now.minute
                return (9 * 60 + 30 <= t <= 12 * 60) or (13 * 60 <= t <= 16 * 60)
            else:
                us_tz = pytz.timezone('US/Eastern')
                now = datetime.now(us_tz)
                if now.weekday() >= 5:
                    return False
                t = now.hour * 60 + now.minute
                return 4 * 60 <= t <= 20 * 60
        except Exception:
            return True
    
    def _is_exit_pending_cooldown(self, symbol: str) -> bool:
        """Check if a symbol is in exit-pending cooldown (submitted and waiting, or recently failed)."""
        if symbol not in self._exit_pending:
            return False
        pending = self._exit_pending[symbol]
        if pending.get("submitted"):
            submitted_at = pending.get("last_attempt", datetime.now())
            age = (datetime.now() - submitted_at).total_seconds()
            if age > 300:
                pending["submitted"] = False
                pending["retry_count"] = pending.get("retry_count", 0) + 1
                pending["last_attempt"] = datetime.now()
                self.logger.warning(
                    f"{symbol} exit_pending 已超时 {age:.0f}s，标记为失败 "
                    f"(重试 {pending['retry_count']}/{self._exit_max_retries})"
                )
                if pending["retry_count"] >= self._exit_max_retries:
                    return True
                return False
            return True
        if pending["retry_count"] >= self._exit_max_retries:
            return True
        elapsed = datetime.now() - pending["last_attempt"]
        return elapsed < self._exit_retry_cooldown
    
    def is_near_exit(self, symbol: str) -> bool:
        """检查标的是否即将触发止盈/止损退出（供 ensemble 在买入前调用）。

        Returns True if:
        - 该标的有 exit_pending（已提交退出订单或即将退出）
        - 该标的浮亏已超过追踪止损阈值的 60%（即将触发止损）
        """
        if symbol in self._exit_pending:
            return True

        status = self.position_status.get(symbol)
        if status and status.unrealized_pnl_pct is not None:
            t = self._get_thresholds(symbol)
            if status.unrealized_pnl_pct <= -t["trailing_stop_pct"] * 0.6:
                return True

        return False

    def clear_exit_pending(self, symbol: str):
        """Clear exit-pending state for a symbol (e.g. after manual intervention)."""
        self._exit_pending.pop(symbol, None)
    
    def on_order_completed(self, order_id: str, symbol: str, is_filled: bool):
        """Called when a profit-stop order reaches terminal state (filled/rejected/canceled)."""
        if symbol not in self._exit_pending:
            return
        pending = self._exit_pending[symbol]
        if pending.get("order_id") != order_id:
            return
        if is_filled:
            self.logger.info(f"止盈止损订单已成交，清除 pending: {symbol}, 订单={order_id}")
            self._exit_pending.pop(symbol, None)
        else:
            pending["submitted"] = False
            pending["retry_count"] = pending.get("retry_count", 0) + 1
            pending["last_attempt"] = datetime.now()
            self.logger.warning(
                f"止盈止损订单未成交(rejected/canceled)，允许重试: {symbol}, "
                f"订单={order_id}, 重试次数={pending['retry_count']}/{self._exit_max_retries}"
            )
    
    def _reset_retries_on_market_open(self):
        """当市场开盘时，重置已达最大重试次数的退出尝试。

        避免在盘后/盘前因连续 Rejected 耗尽重试次数后，
        正式开盘时无法重新提交止盈止损订单。
        """
        stale = []
        for symbol, pending in self._exit_pending.items():
            if pending.get("retry_count", 0) < self._exit_max_retries:
                continue
            if not self._is_market_open(symbol):
                continue
            last_attempt = pending.get("last_attempt", datetime.now())
            age_min = (datetime.now() - last_attempt).total_seconds() / 60
            if age_min > 30:
                stale.append(symbol)
        for symbol in stale:
            self.logger.info(
                f"市场已开盘且距上次尝试超过30分钟，重置 {symbol} 退出重试计数"
            )
            self._exit_pending.pop(symbol, None)

    async def check_exit_signals(self) -> List[ExitSignal]:
        """检查止盈止损信号"""
        exit_signals = []
        
        try:
            self._reset_retries_on_market_open()
            
            for symbol, status in self.position_status.items():
                if self._is_exit_pending_cooldown(symbol):
                    pending = self._exit_pending[symbol]
                    if pending["retry_count"] >= self._exit_max_retries:
                        self.logger.debug(f"跳过 {symbol} 退出信号: 已达最大重试次数 {self._exit_max_retries}")
                    else:
                        self.logger.debug(f"跳过 {symbol} 退出信号: 冷却期内")
                    continue
                
                if not self._is_market_open(symbol):
                    self.logger.debug(f"跳过 {symbol} 退出信号: 当前非交易时段")
                    continue
                
                signal = None
                if self.profit_enabled:
                    signal = self._check_profit_taking(status)
                
                if signal is None and self.stop_enabled:
                    signal = self._check_stop_loss(status)
                
                if signal:
                    exit_signals.append(signal)
                        
            # 检查单日亏损限制
            daily_loss_signal = await self._check_daily_loss_limit()
            if daily_loss_signal:
                exit_signals.extend(daily_loss_signal)
                
        except Exception as e:
            self.logger.error(f"检查退出信号失败: {e}")
            
        return exit_signals
    
    def _get_thresholds(self, symbol: str) -> dict:
        """返回适用于该标的的止盈止损阈值（杠杆产品使用放宽阈值）"""
        if symbol in self._leveraged_symbols and self._leveraged_overrides:
            ov = self._leveraged_overrides
            return {
                'fixed_profit_pct': ov.get('fixed_profit_pct', self.fixed_profit_pct),
                'partial_profit_pct': ov.get('partial_profit_pct', self.partial_profit_pct),
                'trailing_profit_pct': ov.get('trailing_profit_pct', self.trailing_profit_pct),
                'trailing_profit_step': ov.get('trailing_profit_step', self.trailing_profit_step),
                'fixed_stop_pct': ov.get('fixed_stop_pct', self.fixed_stop_pct),
                'emergency_stop_pct': ov.get('emergency_stop_pct', self.emergency_stop_pct),
                'trailing_stop_pct': ov.get('trailing_stop_pct', self.trailing_stop_pct),
            }
        return {
            'fixed_profit_pct': self.fixed_profit_pct,
            'partial_profit_pct': self.partial_profit_pct,
            'trailing_profit_pct': self.trailing_profit_pct,
            'trailing_profit_step': self.trailing_profit_step,
            'fixed_stop_pct': self.fixed_stop_pct,
            'emergency_stop_pct': self.emergency_stop_pct,
            'trailing_stop_pct': self.trailing_stop_pct,
        }

    def _check_profit_taking(self, status: PositionStatus) -> Optional[ExitSignal]:
        """检查止盈信号"""
        try:
            t = self._get_thresholds(status.symbol)

            if status.unrealized_pnl_pct >= t['fixed_profit_pct']:
                return ExitSignal(
                    symbol=status.symbol,
                    signal_type="TAKE_PROFIT",
                    quantity=status.quantity,
                    price=status.current_price,
                    reason=f"达到固定止盈点{t['fixed_profit_pct']}%，当前盈利{status.unrealized_pnl_pct:.2f}%",
                    urgency=7
                )
            
            elif status.unrealized_pnl_pct >= t['partial_profit_pct']:
                is_short = status.quantity < 0
                abs_quantity = abs(status.quantity)
                partial_abs = max(1, abs_quantity // 2)
                partial_quantity = -partial_abs if is_short else partial_abs
                return ExitSignal(
                    symbol=status.symbol,
                    signal_type="PARTIAL_PROFIT",
                    quantity=partial_quantity,
                    price=status.current_price,
                    reason=f"达到部分止盈点{t['partial_profit_pct']}%，平仓50%仓位",
                    urgency=5
                )
            
            elif status.unrealized_pnl_pct >= t['trailing_profit_pct']:
                drawdown_from_high = ((status.highest_price - status.current_price) / status.highest_price) * 100
                if drawdown_from_high >= t['trailing_profit_step']:
                    return ExitSignal(
                        symbol=status.symbol,
                        signal_type="TRAILING_PROFIT",
                        quantity=status.quantity,
                        price=status.current_price,
                        reason=f"追踪止盈触发，从最高点{status.highest_price:.2f}回调{drawdown_from_high:.2f}%",
                        urgency=6
                    )
            
            return None
            
        except Exception as e:
            self.logger.error(f"检查止盈信号失败: {status.symbol}, 错误: {e}")
            return None
    
    def _check_stop_loss(self, status: PositionStatus) -> Optional[ExitSignal]:
        """检查止损信号"""
        try:
            t = self._get_thresholds(status.symbol)

            if status.unrealized_pnl_pct <= -t['emergency_stop_pct']:
                return ExitSignal(
                    symbol=status.symbol,
                    signal_type="EMERGENCY_STOP",
                    quantity=status.quantity,
                    price=status.current_price,
                    reason=f"触发紧急止损，亏损{abs(status.unrealized_pnl_pct):.2f}%",
                    urgency=10
                )
            
            elif status.unrealized_pnl_pct <= -t['fixed_stop_pct']:
                return ExitSignal(
                    symbol=status.symbol,
                    signal_type="STOP_LOSS",
                    quantity=status.quantity,
                    price=status.current_price,
                    reason=f"达到固定止损点{t['fixed_stop_pct']}%，当前亏损{abs(status.unrealized_pnl_pct):.2f}%",
                    urgency=8
                )
            
            # 追踪止损（区分多空仓位）
            is_short = status.quantity < 0
            if is_short:
                # 空头仓位：价格上涨到止损价以上时触发
                if status.current_price >= status.trailing_stop_price:
                    return ExitSignal(
                        symbol=status.symbol,
                        signal_type="TRAILING_STOP",
                        quantity=status.quantity,
                        price=status.current_price,
                        reason=f"触发空头追踪止损，当前价{status.current_price:.2f} >= 止损价{status.trailing_stop_price:.2f}",
                        urgency=9
                    )
            else:
                # 多头仓位：价格下跌到止损价以下时触发
                if status.current_price <= status.trailing_stop_price:
                    return ExitSignal(
                        symbol=status.symbol,
                        signal_type="TRAILING_STOP",
                        quantity=status.quantity,
                        price=status.current_price,
                        reason=f"触发多头追踪止损，当前价{status.current_price:.2f} <= 止损价{status.trailing_stop_price:.2f}",
                        urgency=9
                    )
            
            return None
            
        except Exception as e:
            self.logger.error(f"检查止损信号失败: {status.symbol}, 错误: {e}")
            return None
    
    async def _check_daily_loss_limit(self) -> List[ExitSignal]:
        """检查单日亏损限制"""
        try:
            # 重置日期检查
            now = datetime.now()
            if now.date() > self.daily_pnl_reset_time.date():
                self.daily_pnl = 0.0
                self.daily_pnl_reset_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # 计算当日盈亏
            current_daily_pnl = sum(status.unrealized_pnl for status in self.position_status.values())
            total_portfolio_value = await self._get_total_portfolio_value()
            
            if total_portfolio_value > 0:
                daily_loss_pct = (current_daily_pnl / total_portfolio_value) * 100
                
                if daily_loss_pct <= -self.max_loss_per_day:
                    signals = []
                    for status in self.position_status.values():
                        if status.quantity > 0:
                            signals.append(ExitSignal(
                                symbol=status.symbol,
                                signal_type="DAILY_LOSS_LIMIT",
                                quantity=status.quantity,
                                price=status.current_price,
                                reason=f"达到单日亏损限制{self.max_loss_per_day}%，当前亏损{abs(daily_loss_pct):.2f}%",
                                urgency=10
                            ))
                        elif status.quantity < 0:
                            signals.append(ExitSignal(
                                symbol=status.symbol,
                                signal_type="DAILY_LOSS_LIMIT",
                                quantity=status.quantity,
                                price=status.current_price,
                                reason=f"达到单日亏损限制{self.max_loss_per_day}%，当前亏损{abs(daily_loss_pct):.2f}%（空头平仓）",
                                urgency=10
                            ))
                    
                    self.logger.warning(f"触发单日亏损限制，清仓所有持仓（多头{sum(1 for s in signals if s.quantity > 0)}笔, 空头{sum(1 for s in signals if s.quantity < 0)}笔）")
                    return signals
            
            return []
            
        except Exception as e:
            self.logger.error(f"检查单日亏损限制失败: {e}")
            return []
    
    async def _get_total_portfolio_value(self) -> float:
        """获取投资组合总市值"""
        try:
            total_value = 0.0
            for status in self.position_status.values():
                total_value += status.current_price * status.quantity
            return total_value
        except Exception as e:
            self.logger.error(f"计算投资组合总市值失败: {e}")
            return 0.0
    
    async def execute_exit_signal(self, signal: ExitSignal) -> bool:
        """执行退出信号"""
        try:
            is_short = signal.quantity < 0
            exit_quantity = abs(signal.quantity)
            
            is_emergency = signal.signal_type in ("EMERGENCY_STOP", "DAILY_LOSS_LIMIT")
            if is_emergency:
                slippage = 0.02
                if is_short:
                    signal.price = signal.price * (1 + slippage)
                else:
                    signal.price = signal.price * (1 - slippage)
                self.logger.warning(
                    f"紧急退出 {signal.symbol}: 应用 {slippage:.0%} 滑点容忍, "
                    f"调整价格至 {signal.price:.2f}"
                )
            
            if is_short:
                # 估算平仓所需资金
                estimated_cost = signal.price * exit_quantity
                
                # 获取账户资金信息
                try:
                    account_info = self.order_manager.get_account_info()
                    if hasattr(account_info, "total_cash") and account_info.total_cash is not None:
                        total_cash = account_info.total_cash
                        
                        # 如果总现金为负，无法进行买入交易
                        if total_cash < 0:
                            self.logger.warning(f"账户总现金为负({total_cash:.2f})，无法平仓空头仓位: {signal.symbol}")
                            return False
                        
                        # 检查是否有足够资金
                        if total_cash < estimated_cost:
                            self.logger.warning(f"账户资金不足({total_cash:.2f} < {estimated_cost:.2f})，无法平仓空头仓位: {signal.symbol}")
                            return False
                except Exception as e:
                    self.logger.warning(f"获取账户信息失败，继续尝试平仓: {e}")
            
                self.logger.info(f"执行空头平仓信号: {signal.symbol} {signal.signal_type} 买入{exit_quantity}股, 原因: {signal.reason}")
                result = await self.order_manager.submit_buy_order(
                    symbol=signal.symbol,
                    price=signal.price,
                    quantity=exit_quantity,
                    strategy_name=f"profit_stop_{signal.signal_type.lower()}_cover"
                )
            else:
                # 多头仓位：使用卖出订单平仓
                self.logger.info(f"执行多头平仓信号: {signal.symbol} {signal.signal_type} 卖出{exit_quantity}股, 原因: {signal.reason}")
                result = await self.order_manager.submit_sell_order(
                    symbol=signal.symbol,
                    price=signal.price,
                    quantity=exit_quantity,
                    strategy_name=f"profit_stop_{signal.signal_type.lower()}"
                )
            
            if result and not result.is_rejected():
                self.logger.info(f"止盈止损订单提交成功: {signal.symbol}, 订单ID: {result.order_id}")
                
                existing = self._exit_pending.get(signal.symbol, {})
                self._exit_pending[signal.symbol] = {
                    "last_attempt": datetime.now(),
                    "retry_count": existing.get("retry_count", 0),
                    "signal_type": signal.signal_type,
                    "order_id": result.order_id,
                    "submitted": True,
                }
                
                return True
            else:
                pending = self._exit_pending.get(signal.symbol, {"retry_count": 0, "signal_type": signal.signal_type})
                pending["last_attempt"] = datetime.now()
                pending["retry_count"] = pending.get("retry_count", 0) + 1
                pending["signal_type"] = signal.signal_type
                self._exit_pending[signal.symbol] = pending
                self.logger.warning(
                    f"止盈止损订单提交失败: {signal.symbol}, 结果: {result}, "
                    f"重试次数: {pending['retry_count']}/{self._exit_max_retries}"
                )
                return False
                
        except Exception as e:
            self.logger.error(f"执行退出信号失败: {signal.symbol}, 错误: {e}")
            return False
    
    def get_status_summary(self) -> Dict:
        """获取状态摘要"""
        try:
            total_positions = len(self.position_status)
            total_unrealized_pnl = sum(status.unrealized_pnl for status in self.position_status.values())
            
            profitable_positions = sum(1 for status in self.position_status.values() if status.unrealized_pnl > 0)
            losing_positions = sum(1 for status in self.position_status.values() if status.unrealized_pnl < 0)
            
            return {
                'total_positions': total_positions,
                'profitable_positions': profitable_positions,
                'losing_positions': losing_positions,
                'total_unrealized_pnl': total_unrealized_pnl,
                'daily_pnl': self.daily_pnl,
                'profit_enabled': self.profit_enabled,
                'stop_enabled': self.stop_enabled
            }
            
        except Exception as e:
            self.logger.error(f"获取状态摘要失败: {e}")
            return {} 