"""
资金守卫
中央化的资金检查机制，加强资金保护
"""

import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Tuple, Optional, Dict, List
from collections import defaultdict


class FundGuard:
    """
    资金守卫 - 中央化的资金检查机制
    
    功能：
    1. 负余额保护 - 余额为负时拒绝所有买入交易
    2. 最小储备金检查 - 确保保留一定金额的储备金
    3. 单笔交易限制 - 限制单笔交易占账户权益的比例
    4. 日亏损限制 - 当日亏损达到阈值时暂停交易
    5. 总仓位限制 - 限制总仓位占账户权益的比例
    6. 交易金额统计 - 记录每日交易金额
    """
    
    def __init__(self, order_manager, config, logger=None):
        """
        初始化资金守卫
        
        Args:
            order_manager: 订单管理器实例
            config: 配置对象
            logger: 日志记录器
        """
        self.order_manager = order_manager
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # 配置参数
        self.min_reserve = Decimal(str(config.get("execution.min_reserve", 1000)))  # 最小储备金
        self.max_single_trade_pct = Decimal(str(config.get("execution.risk_control.position_pct", 5.0))) / 100  # 单笔最大比例
        self.max_daily_loss_pct = Decimal(str(config.get("execution.risk_control.max_daily_loss_pct", 3.0))) / 100  # 日亏损限制
        self.max_total_position_pct = Decimal(str(config.get("execution.risk_control.max_total_position_pct", 80.0))) / 100  # 总仓位限制
        
        # 日内交易统计
        self.daily_trades: Dict[str, List[Dict]] = defaultdict(list)  # {date_str: [trade_info, ...]}
        self.daily_pnl: Dict[str, Decimal] = defaultdict(Decimal)  # {date_str: pnl}
        
        # 交易暂停状态
        self.trading_paused = False
        self.pause_reason = ""
        self.pause_until: Optional[datetime] = None
        
        self.logger.info(f"资金守卫初始化完成 - 储备金: ${self.min_reserve}, "
                        f"单笔限制: {self.max_single_trade_pct:.1%}, "
                        f"日亏损限制: {self.max_daily_loss_pct:.1%}, "
                        f"总仓位限制: {self.max_total_position_pct:.1%}")
    
    def can_trade(self, symbol: str, side: str, amount: Decimal, quantity: int = 0) -> Tuple[bool, str]:
        """
        检查是否可以交易
        
        Args:
            symbol: 股票代码
            side: 交易方向 ('Buy' 或 'Sell')
            amount: 交易金额
            quantity: 交易数量（可选）
            
        Returns:
            Tuple[bool, str]: (是否可以交易, 原因说明)
        """
        try:
            # 0. 检查交易是否暂停
            if self.trading_paused:
                if self.pause_until and datetime.now() > self.pause_until:
                    # 暂停时间已过，自动恢复
                    self.resume_trading()
                else:
                    return False, f"交易已暂停: {self.pause_reason}"
            
            # 获取账户余额
            balance = Decimal(str(self.order_manager.get_account_balance()))
            
            # 1. 检查负余额
            if balance <= 0:
                self.logger.warning(f"❌ 负余额保护触发: 余额={balance}")
                return False, f"账户余额为负或零: ${balance:.2f}"
            
            # 对于卖出订单，不需要检查资金
            if side.upper() == 'SELL':
                return True, "卖出订单通过资金检查"
            
            # 以下检查仅针对买入订单
            
            # 2. 检查最小储备金
            available = balance - self.min_reserve
            if available <= 0:
                return False, f"可用资金不足: 余额${balance:.2f}, 储备金${self.min_reserve}"
            
            if amount > available:
                return False, f"交易金额超过可用资金: ${amount:.2f} > ${available:.2f}"
            
            # 3. 检查单笔交易限制
            total_equity = self._get_total_equity()
            if total_equity > 0:
                max_single_trade = total_equity * self.max_single_trade_pct
                if amount > max_single_trade:
                    return False, f"超过单笔交易限制: ${amount:.2f} > ${max_single_trade:.2f} ({self.max_single_trade_pct:.1%})"
            
            # 4. 检查日亏损限制
            daily_loss_check, daily_loss_msg = self._check_daily_loss_limit(total_equity)
            if not daily_loss_check:
                self.pause_trading(daily_loss_msg, hours=1)  # 暂停1小时
                return False, daily_loss_msg
            
            # 5. 检查总仓位限制
            position_check, position_msg = self._check_total_position_limit(amount, total_equity)
            if not position_check:
                return False, position_msg
            
            return True, "通过资金检查"
            
        except Exception as e:
            self.logger.error(f"资金检查异常: {e}")
            return False, f"资金检查异常: {str(e)}"
    
    def _get_total_equity(self) -> Decimal:
        """
        获取账户总权益
        
        Returns:
            总权益（现金 + 持仓市值）
        """
        try:
            balance = Decimal(str(self.order_manager.get_account_balance()))
            positions = self.order_manager.get_positions()
            
            position_value = Decimal('0')
            for pos in positions:
                qty = Decimal(str(getattr(pos, 'quantity', 0)))
                cost = Decimal(str(getattr(pos, 'cost_price', 0)))
                if qty > 0 and cost > 0:
                    position_value += qty * cost
            
            total = max(Decimal('0'), balance + position_value)
            return total
            
        except Exception as e:
            self.logger.error(f"获取总权益失败: {e}")
            # 返回余额作为后备
            try:
                return Decimal(str(self.order_manager.get_account_balance()))
            except:
                return Decimal('0')
    
    def _get_total_position_value(self) -> Decimal:
        """
        获取当前总持仓市值
        
        Returns:
            总持仓市值
        """
        try:
            positions = self.order_manager.get_positions()
            
            total_value = Decimal('0')
            for pos in positions:
                qty = Decimal(str(abs(getattr(pos, 'quantity', 0))))
                cost = Decimal(str(abs(getattr(pos, 'cost_price', 0))))
                if qty > 0 and cost > 0:
                    total_value += qty * cost
            
            return total_value
            
        except Exception as e:
            self.logger.error(f"获取持仓市值失败: {e}")
            return Decimal('0')
    
    def _check_daily_loss_limit(self, total_equity: Decimal) -> Tuple[bool, str]:
        """
        检查日亏损限制
        
        Args:
            total_equity: 总权益
            
        Returns:
            Tuple[bool, str]: (是否通过, 消息)
        """
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            daily_pnl = self.daily_pnl.get(today, Decimal('0'))
            
            if total_equity <= 0:
                return True, "总权益为零，跳过日亏损检查"
            
            max_daily_loss = total_equity * self.max_daily_loss_pct
            
            if daily_pnl < -max_daily_loss:
                return False, f"已达日亏损限制: ${daily_pnl:.2f} < -${max_daily_loss:.2f} ({self.max_daily_loss_pct:.1%})"
            
            return True, f"日亏损正常: ${daily_pnl:.2f} / -${max_daily_loss:.2f}"
            
        except Exception as e:
            self.logger.error(f"检查日亏损限制失败: {e}")
            return True, f"日亏损检查异常: {str(e)}"
    
    def _check_total_position_limit(self, new_amount: Decimal, total_equity: Decimal) -> Tuple[bool, str]:
        """
        检查总仓位限制
        
        Args:
            new_amount: 新交易金额
            total_equity: 总权益
            
        Returns:
            Tuple[bool, str]: (是否通过, 消息)
        """
        try:
            if total_equity <= 0:
                return True, "总权益为零，跳过仓位检查"
            
            current_position = self._get_total_position_value()
            new_total_position = current_position + new_amount
            
            max_position = total_equity * self.max_total_position_pct
            
            if new_total_position > max_position:
                return False, f"超过总仓位限制: ${new_total_position:.2f} > ${max_position:.2f} ({self.max_total_position_pct:.1%})"
            
            current_pct = (new_total_position / total_equity) * 100
            return True, f"仓位正常: {current_pct:.1f}% / {self.max_total_position_pct*100:.0f}%"
            
        except Exception as e:
            self.logger.error(f"检查仓位限制失败: {e}")
            return True, f"仓位检查异常: {str(e)}"
    
    def record_trade(self, symbol: str, side: str, amount: float, pnl: float = 0):
        """
        记录交易
        
        Args:
            symbol: 股票代码
            side: 交易方向
            amount: 交易金额
            pnl: 盈亏（卖出时提供）
        """
        today = datetime.now().strftime("%Y-%m-%d")
        
        trade_info = {
            'symbol': symbol,
            'side': side,
            'amount': amount,
            'pnl': pnl,
            'timestamp': datetime.now().isoformat()
        }
        
        self.daily_trades[today].append(trade_info)
        
        if pnl != 0:
            self.daily_pnl[today] += Decimal(str(pnl))
            self.logger.debug(f"记录交易盈亏: {symbol} {side} PnL=${pnl:.2f}, 日累计=${self.daily_pnl[today]:.2f}")
    
    def pause_trading(self, reason: str, hours: float = 1):
        """
        暂停交易
        
        Args:
            reason: 暂停原因
            hours: 暂停时长（小时）
        """
        self.trading_paused = True
        self.pause_reason = reason
        self.pause_until = datetime.now() + timedelta(hours=hours)
        
        self.logger.warning(f"⚠️ 交易已暂停: {reason}, 恢复时间: {self.pause_until.strftime('%H:%M:%S')}")
    
    def resume_trading(self):
        """恢复交易"""
        self.trading_paused = False
        self.pause_reason = ""
        self.pause_until = None
        
        self.logger.info("✅ 交易已恢复")
    
    def get_daily_stats(self, date: Optional[str] = None) -> Dict:
        """
        获取每日交易统计
        
        Args:
            date: 日期字符串，默认为今天
            
        Returns:
            统计信息字典
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        trades = self.daily_trades.get(date, [])
        pnl = self.daily_pnl.get(date, Decimal('0'))
        
        buy_count = len([t for t in trades if t['side'].upper() == 'BUY'])
        sell_count = len([t for t in trades if t['side'].upper() == 'SELL'])
        
        buy_amount = sum(t['amount'] for t in trades if t['side'].upper() == 'BUY')
        sell_amount = sum(t['amount'] for t in trades if t['side'].upper() == 'SELL')
        
        return {
            'date': date,
            'trade_count': len(trades),
            'buy_count': buy_count,
            'sell_count': sell_count,
            'buy_amount': buy_amount,
            'sell_amount': sell_amount,
            'pnl': float(pnl),
            'is_paused': self.trading_paused,
            'pause_reason': self.pause_reason
        }
    
    def reset_daily_stats(self):
        """重置每日统计（新的一天开始时调用）"""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        # 只保留今天的数据
        if yesterday in self.daily_trades:
            del self.daily_trades[yesterday]
        if yesterday in self.daily_pnl:
            del self.daily_pnl[yesterday]
        
        self.logger.info(f"每日统计已重置: {today}")
    
    def get_summary(self) -> str:
        """
        获取资金守卫状态摘要
        
        Returns:
            格式化的摘要字符串
        """
        lines = ["💰 资金守卫状态摘要:"]
        
        try:
            balance = self.order_manager.get_account_balance()
            equity = float(self._get_total_equity())
            position = float(self._get_total_position_value())
            
            lines.append(f"   账户余额: ${balance:.2f}")
            lines.append(f"   总权益: ${equity:.2f}")
            lines.append(f"   持仓市值: ${position:.2f}")
            lines.append(f"   仓位比例: {(position/equity*100) if equity > 0 else 0:.1f}% / {self.max_total_position_pct*100:.0f}%")
        except Exception as e:
            lines.append(f"   获取账户信息失败: {e}")
        
        lines.append(f"   最小储备金: ${self.min_reserve}")
        lines.append(f"   单笔限制: {self.max_single_trade_pct:.1%}")
        lines.append(f"   日亏损限制: {self.max_daily_loss_pct:.1%}")
        
        if self.trading_paused:
            lines.append(f"   ⚠️ 交易已暂停: {self.pause_reason}")
            if self.pause_until:
                lines.append(f"   恢复时间: {self.pause_until.strftime('%H:%M:%S')}")
        else:
            lines.append("   ✅ 交易正常")
        
        # 今日统计
        stats = self.get_daily_stats()
        lines.append(f"   今日交易: {stats['trade_count']}笔 (买{stats['buy_count']}/卖{stats['sell_count']})")
        lines.append(f"   今日盈亏: ${stats['pnl']:.2f}")
        
        return "\n".join(lines)
    
    def force_check(self) -> Tuple[bool, str]:
        """
        强制进行全面资金检查
        
        Returns:
            Tuple[bool, str]: (是否健康, 状态描述)
        """
        issues = []
        
        try:
            balance = Decimal(str(self.order_manager.get_account_balance()))
            equity = self._get_total_equity()
            
            # 检查负余额
            if balance <= 0:
                issues.append(f"负余额: ${balance:.2f}")
            
            # 检查储备金
            if balance < self.min_reserve:
                issues.append(f"低于储备金: ${balance:.2f} < ${self.min_reserve}")
            
            # 检查日亏损
            daily_check, daily_msg = self._check_daily_loss_limit(equity)
            if not daily_check:
                issues.append(daily_msg)
            
            # 检查仓位
            position_value = self._get_total_position_value()
            if equity > 0:
                position_pct = position_value / equity
                if position_pct > self.max_total_position_pct:
                    issues.append(f"仓位过高: {position_pct:.1%} > {self.max_total_position_pct:.1%}")
            
            if issues:
                return False, "; ".join(issues)
            else:
                return True, "资金状态健康"
                
        except Exception as e:
            return False, f"检查异常: {str(e)}"
