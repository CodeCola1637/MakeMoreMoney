"""
止盈止损管理器
负责监控持仓的盈亏状况，并在适当时机触发止盈止损操作
"""

import asyncio
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
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
        
        # 持仓状态追踪
        self.position_status: Dict[str, PositionStatus] = {}
        self.daily_pnl = 0.0
        self.daily_pnl_reset_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
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
            if symbol not in self.position_status:
                self.position_status[symbol] = PositionStatus(
                    symbol=symbol,
                    quantity=quantity,
                    cost_price=float(cost_price),
                    current_price=float(current_price),
                    unrealized_pnl=float(unrealized_pnl),
                    unrealized_pnl_pct=float(unrealized_pnl_pct),
                    highest_price=float(current_price),
                    trailing_stop_price=float(cost_price) * (1 - self.trailing_stop_pct / 100),
                    last_update=datetime.now()
                )
                self.logger.info(f"创建持仓状态跟踪: {symbol}, 成本价: {cost_price}, 当前价: {current_price}")
            else:
                status = self.position_status[symbol]
                status.quantity = quantity
                status.current_price = float(current_price)
                status.unrealized_pnl = float(unrealized_pnl)
                status.unrealized_pnl_pct = float(unrealized_pnl_pct)
                status.last_update = datetime.now()
                
                # 更新追踪止损逻辑（区分多空仓位）
                is_short = quantity < 0
                if is_short:
                    # 空头仓位：追踪最低价，止损在上方
                    if float(current_price) < status.highest_price:
                        status.highest_price = float(current_price)  # 对于空头，这里存储的是最低价
                        # 更新追踪止损价格（在最低价上方）
                        new_trailing_stop = float(current_price) * (1 + self.trailing_stop_pct / 100)
                        if new_trailing_stop < status.trailing_stop_price:
                            status.trailing_stop_price = new_trailing_stop
                            self.logger.debug(f"更新空头追踪止损价格: {symbol}, 新止损价: {new_trailing_stop:.2f}")
                else:
                    # 多头仓位：追踪最高价，止损在下方
                    if float(current_price) > status.highest_price:
                        status.highest_price = float(current_price)
                        # 更新追踪止损价格
                        new_trailing_stop = float(current_price) * (1 - self.trailing_stop_pct / 100)
                        if new_trailing_stop > status.trailing_stop_price:
                            status.trailing_stop_price = new_trailing_stop
                            self.logger.debug(f"更新多头追踪止损价格: {symbol}, 新止损价: {new_trailing_stop:.2f}")
                
                self.logger.debug(f"更新持仓状态: {symbol}, 盈亏: {unrealized_pnl_pct:.2f}%, 最高价: {status.highest_price:.2f}")
                
        except Exception as e:
            self.logger.error(f"更新持仓状态失败: {symbol}, 错误: {e}")
    
    async def check_exit_signals(self) -> List[ExitSignal]:
        """检查止盈止损信号"""
        exit_signals = []
        
        try:
            for symbol, status in self.position_status.items():
                # 检查止盈信号
                if self.profit_enabled:
                    profit_signal = self._check_profit_taking(status)
                    if profit_signal:
                        exit_signals.append(profit_signal)
                
                # 检查止损信号
                if self.stop_enabled:
                    stop_signal = self._check_stop_loss(status)
                    if stop_signal:
                        exit_signals.append(stop_signal)
                        
            # 检查单日亏损限制
            daily_loss_signal = await self._check_daily_loss_limit()
            if daily_loss_signal:
                exit_signals.extend(daily_loss_signal)
                
        except Exception as e:
            self.logger.error(f"检查退出信号失败: {e}")
            
        return exit_signals
    
    def _check_profit_taking(self, status: PositionStatus) -> Optional[ExitSignal]:
        """检查止盈信号"""
        try:
            # 固定止盈点
            if status.unrealized_pnl_pct >= self.fixed_profit_pct:
                return ExitSignal(
                    symbol=status.symbol,
                    signal_type="TAKE_PROFIT",
                    quantity=status.quantity,  # 全部卖出
                    price=status.current_price,
                    reason=f"达到固定止盈点{self.fixed_profit_pct}%，当前盈利{status.unrealized_pnl_pct:.2f}%",
                    urgency=7
                )
            
            # 部分止盈
            elif status.unrealized_pnl_pct >= self.partial_profit_pct:
                # 使用绝对值计算部分数量，保持原符号
                is_short = status.quantity < 0
                abs_quantity = abs(status.quantity)
                partial_abs = max(1, abs_quantity // 2)  # 平仓一半
                partial_quantity = -partial_abs if is_short else partial_abs
                return ExitSignal(
                    symbol=status.symbol,
                    signal_type="PARTIAL_PROFIT",
                    quantity=partial_quantity,
                    price=status.current_price,
                    reason=f"达到部分止盈点{self.partial_profit_pct}%，平仓50%仓位",
                    urgency=5
                )
            
            # 追踪止盈（从最高点回调）
            elif status.unrealized_pnl_pct >= self.trailing_profit_pct:
                drawdown_from_high = ((status.highest_price - status.current_price) / status.highest_price) * 100
                if drawdown_from_high >= self.trailing_profit_step:
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
            # 紧急止损
            if status.unrealized_pnl_pct <= -self.emergency_stop_pct:
                return ExitSignal(
                    symbol=status.symbol,
                    signal_type="EMERGENCY_STOP",
                    quantity=status.quantity,
                    price=status.current_price,
                    reason=f"触发紧急止损，亏损{abs(status.unrealized_pnl_pct):.2f}%",
                    urgency=10
                )
            
            # 固定止损
            elif status.unrealized_pnl_pct <= -self.fixed_stop_pct:
                return ExitSignal(
                    symbol=status.symbol,
                    signal_type="STOP_LOSS",
                    quantity=status.quantity,
                    price=status.current_price,
                    reason=f"达到固定止损点{self.fixed_stop_pct}%，当前亏损{abs(status.unrealized_pnl_pct):.2f}%",
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
                    # 生成所有持仓的卖出信号
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
                    
                    self.logger.warning(f"触发单日亏损限制，清仓所有持仓")
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
            # 判断是多头还是空头仓位
            is_short = signal.quantity < 0
            exit_quantity = abs(signal.quantity)  # 使用绝对值
            
                        # 检查账户资金状况（对于空头平仓需要买入）
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
                
                # 更新持仓状态
                if signal.symbol in self.position_status:
                    if is_short:
                        # 空头平仓：数量增加（从负数向0）
                        self.position_status[signal.symbol].quantity += exit_quantity
                    else:
                        # 多头平仓：数量减少
                        self.position_status[signal.symbol].quantity -= exit_quantity
                    
                    # 仓位清零后删除状态跟踪
                    if self.position_status[signal.symbol].quantity == 0:
                        del self.position_status[signal.symbol]
                        self.logger.info(f"清空持仓状态跟踪: {signal.symbol}")
                
                return True
            else:
                self.logger.warning(f"止盈止损订单提交失败: {signal.symbol}, 结果: {result}")
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