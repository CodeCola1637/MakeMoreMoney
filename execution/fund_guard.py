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
        
        # 单一标的最大持仓比例
        self.max_single_position_pct = Decimal(str(config.get("execution.risk_control.max_single_position_pct", 20.0))) / 100
        
        # 保证金保护参数
        self.max_leverage = Decimal(str(config.get("execution.risk_control.max_leverage", 2.0)))
        self.margin_warning_pct = Decimal(str(config.get("execution.risk_control.margin_warning_pct", 40.0))) / 100
        self.margin_danger_pct = Decimal(str(config.get("execution.risk_control.margin_danger_pct", 30.0))) / 100
        
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
                        f"总仓位限制: {self.max_total_position_pct:.1%}, "
                        f"单标的上限: {self.max_single_position_pct:.1%}, "
                        f"最大杠杆: {self.max_leverage}x")
    
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
                    is_min_lot = self._is_minimum_lot_order(symbol, quantity, amount)
                    if is_min_lot:
                        self.logger.info(f"单笔超限但为最小手数，放行: {symbol} ${amount:.2f} > ${max_single_trade:.2f}")
                    else:
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
            
            # 5.5 单一标的持仓集中度检查
            single_check, single_msg = self._check_single_position_limit(symbol, amount, total_equity)
            if not single_check:
                return False, single_msg
            
            # 6. 保证金/杠杆保护
            margin_check, margin_msg = self._check_margin_safety(amount)
            if not margin_check:
                return False, margin_msg
            
            return True, "通过资金检查"
            
        except Exception as e:
            self.logger.error(f"资金检查异常: {e}")
            return False, f"资金检查异常: {str(e)}"
    
    def _is_minimum_lot_order(self, symbol: str, quantity: int, amount: Decimal) -> bool:
        """判断是否为最小手数订单（1手）"""
        try:
            lot_size = self.order_manager.get_lot_size(symbol)
            return quantity <= lot_size
        except Exception:
            pass
        if symbol.endswith('.HK'):
            lot = self.config.get("execution.lot_sizes", {}).get(symbol, 100)
            return quantity <= lot
        return quantity <= 1

    def _get_total_equity(self) -> Decimal:
        """获取账户真实净资产（优先使用券商 net_assets，避免 buy_power 虚高）"""
        try:
            margin_info = self.order_manager.get_margin_info()
            if margin_info and margin_info.get("available") and margin_info["net_assets"] > 0:
                return Decimal(str(margin_info["net_assets"]))
        except Exception:
            pass

        try:
            balance = Decimal(str(self.order_manager.get_account_balance()))
            positions = self.order_manager.get_positions()
            
            position_value = Decimal('0')
            for pos in positions:
                qty = Decimal(str(getattr(pos, 'quantity', 0)))
                if qty <= 0:
                    continue
                market_val = getattr(pos, 'market_val', None)
                if market_val and float(market_val) > 0:
                    position_value += Decimal(str(market_val))
                else:
                    cost = Decimal(str(getattr(pos, 'cost_price', 0)))
                    if cost > 0:
                        position_value += qty * cost
            
            total = max(Decimal('0'), balance + position_value)
            return total
            
        except Exception as e:
            self.logger.error(f"获取总权益失败: {e}")
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
                if qty <= 0:
                    continue
                market_val = getattr(pos, 'market_val', None)
                if market_val and float(market_val) > 0:
                    total_value += Decimal(str(abs(float(market_val))))
                else:
                    cost = Decimal(str(abs(getattr(pos, 'cost_price', 0))))
                    if cost > 0:
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
    
    def _check_single_position_limit(self, symbol: str, new_amount: Decimal, total_equity: Decimal) -> Tuple[bool, str]:
        """检查单一标的持仓集中度"""
        try:
            if total_equity <= 0:
                return True, "总权益为零，跳过集中度检查"
            
            current_symbol_value = Decimal('0')
            positions = self.order_manager.get_positions()
            for pos in positions:
                if getattr(pos, 'symbol', '') != symbol:
                    continue
                qty = Decimal(str(abs(getattr(pos, 'quantity', 0))))
                if qty <= 0:
                    continue
                market_val = getattr(pos, 'market_val', None)
                if market_val and float(market_val) > 0:
                    current_symbol_value += Decimal(str(abs(float(market_val))))
                else:
                    cost = Decimal(str(abs(getattr(pos, 'cost_price', 0))))
                    if cost > 0:
                        current_symbol_value += qty * cost
            
            new_symbol_value = current_symbol_value + new_amount
            max_allowed = total_equity * self.max_single_position_pct
            
            if new_symbol_value > max_allowed:
                current_pct = (new_symbol_value / total_equity) * 100
                is_min_lot = self._is_minimum_lot_order(symbol, 0, new_amount)
                if is_min_lot and current_symbol_value == 0:
                    self.logger.info(
                        f"单标的超限但为首次最小手数，放行: {symbol} {current_pct:.1f}%"
                    )
                    return True, f"首次最小手数放行: {symbol}"
                return False, (
                    f"单标的持仓集中度超限: {symbol} 买入后={current_pct:.1f}% > "
                    f"{self.max_single_position_pct*100:.0f}%, "
                    f"持仓${current_symbol_value:.0f}+新买${new_amount:.0f}=${new_symbol_value:.0f}, "
                    f"上限=${max_allowed:.0f}"
                )
            
            return True, f"单标的集中度正常: {symbol}"
        except Exception as e:
            self.logger.error(f"单标的集中度检查异常: {e}")
            return True, f"集中度检查异常: {e}"
    
    def _check_margin_safety(self, new_amount: Decimal) -> Tuple[bool, str]:
        """检查保证金安全：杠杆率上限 + 维持保证金缓冲"""
        try:
            margin_info = self.order_manager.get_margin_info()
            if not margin_info or not margin_info.get("available") or margin_info["net_assets"] <= 0:
                return True, "无保证金数据，跳过检查"

            net_assets = Decimal(str(margin_info["net_assets"]))
            position_value = Decimal(str(margin_info["position_value"]))
            maint_margin = Decimal(str(margin_info["maintenance_margin"]))
            risk_level = margin_info["risk_level"]

            # 1. 券商风险等级检查（risk_level >= 3 为危险）
            if risk_level >= 3:
                self.logger.warning(f"🚨 券商风险等级={risk_level}（危险），禁止买入")
                return False, f"券商风险等级={risk_level}（危险），禁止新买入"
            if risk_level >= 2:
                self.logger.warning(f"⚠️ 券商风险等级={risk_level}（预警），限制买入")
                return False, f"券商风险等级={risk_level}（预警），暂停买入"

            # 2. 杠杆率上限检查
            new_position_value = position_value + new_amount
            new_leverage = new_position_value / net_assets if net_assets > 0 else Decimal('0')
            if new_leverage > self.max_leverage:
                return False, (
                    f"超过杠杆上限: 买入后杠杆={new_leverage:.2f}x > {self.max_leverage}x, "
                    f"持仓={new_position_value:.0f}/净资产={net_assets:.0f}"
                )

            # 3. 维持保证金缓冲检查
            if maint_margin > 0:
                margin_buffer = (net_assets - maint_margin) / net_assets
                if margin_buffer < self.margin_warning_pct:
                    return False, (
                        f"保证金缓冲不足: {margin_buffer:.1%} < {self.margin_warning_pct:.1%}, "
                        f"净资产={net_assets:.0f}, 维持保证金={maint_margin:.0f}"
                    )

            return True, f"保证金安全: 杠杆={new_leverage:.2f}x, 风险等级={risk_level}"

        except Exception as e:
            self.logger.error(f"保证金检查异常: {e}")
            return True, f"保证金检查异常: {e}"

    def check_margin_health(self) -> Dict:
        """全面检查保证金健康状况（供监控任务调用）"""
        result = {
            "healthy": True, "risk_level": 0, "leverage": 0.0,
            "margin_ratio": 0.0, "margin_buffer_pct": 0.0,
            "warnings": [], "actions": [],
        }
        try:
            margin_info = self.order_manager.get_margin_info()
            if not margin_info or not margin_info.get("available") or margin_info["net_assets"] <= 0:
                result["warnings"].append("无法获取保证金数据")
                return result

            net_assets = margin_info["net_assets"]
            maint_margin = margin_info["maintenance_margin"]
            leverage = margin_info["leverage"]
            risk_level = margin_info["risk_level"]
            margin_ratio = margin_info["margin_ratio"]

            result["risk_level"] = risk_level
            result["leverage"] = leverage
            result["margin_ratio"] = margin_ratio

            if maint_margin > 0:
                buffer_pct = (net_assets - maint_margin) / net_assets * 100
                result["margin_buffer_pct"] = buffer_pct
            else:
                buffer_pct = 100.0
                result["margin_buffer_pct"] = buffer_pct

            # 风险分级
            if risk_level >= 3 or buffer_pct < float(self.margin_danger_pct * 100):
                result["healthy"] = False
                result["warnings"].append(
                    f"🚨 保证金危险: 风险等级={risk_level}, 缓冲={buffer_pct:.1f}%, 杠杆={leverage:.2f}x"
                )
                result["actions"].append("REDUCE_POSITION")
            elif risk_level >= 2 or buffer_pct < float(self.margin_warning_pct * 100) or leverage > float(self.max_leverage):
                result["healthy"] = False
                result["warnings"].append(
                    f"⚠️ 保证金预警: 风险等级={risk_level}, 缓冲={buffer_pct:.1f}%, 杠杆={leverage:.2f}x"
                )
                result["actions"].append("STOP_BUYING")
            else:
                self.logger.debug(
                    f"保证金健康: 风险等级={risk_level}, 缓冲={buffer_pct:.1f}%, 杠杆={leverage:.2f}x"
                )

            return result

        except Exception as e:
            self.logger.error(f"保证金健康检查异常: {e}")
            result["warnings"].append(f"检查异常: {e}")
            return result

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
        
        try:
            margin_info = self.order_manager.get_margin_info()
            if margin_info and margin_info.get("available") and margin_info["net_assets"] > 0:
                lines.append(f"   净资产(真实): ${margin_info['net_assets']:.2f}")
                lines.append(f"   杠杆倍数: {margin_info['leverage']:.2f}x / {self.max_leverage}x上限")
                lines.append(f"   维持保证金: ${margin_info['maintenance_margin']:.2f}")
                buffer = margin_info['net_assets'] - margin_info['maintenance_margin']
                buffer_pct = buffer / margin_info['net_assets'] * 100 if margin_info['net_assets'] > 0 else 0
                lines.append(f"   保证金缓冲: ${buffer:.2f} ({buffer_pct:.1f}%)")
                lines.append(f"   券商风险等级: {margin_info['risk_level']}")
        except Exception:
            pass
        
        lines.append(f"   最小储备金: ${self.min_reserve}")
        lines.append(f"   单笔限制: {self.max_single_trade_pct:.1%}")
        lines.append(f"   单标的上限: {self.max_single_position_pct:.1%}")
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
            
            # 检查保证金
            margin_health = self.check_margin_health()
            if not margin_health["healthy"]:
                issues.extend(margin_health["warnings"])
            
            if issues:
                return False, "; ".join(issues)
            else:
                return True, "资金状态健康"
                
        except Exception as e:
            return False, f"检查异常: {str(e)}"
