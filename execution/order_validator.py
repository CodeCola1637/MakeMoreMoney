"""
订单验证器
在提交订单前进行全面的预检查，降低订单拒绝率
"""

import logging
from datetime import datetime
from typing import Tuple, Optional, List
from decimal import Decimal
import pytz


class OrderValidator:
    """
    订单验证器 - 提交订单前的预检查机制
    
    验证内容包括：
    1. 市场交易时间检查
    2. 持仓/资金检查
    3. 价格合理性检查
    4. 最小交易单位检查
    5. 日内订单限制检查
    """
    
    def __init__(self, order_manager, config, logger=None):
        """
        初始化订单验证器
        
        Args:
            order_manager: 订单管理器实例
            config: 配置对象
            logger: 日志记录器
        """
        self.order_manager = order_manager
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        
        # 价格偏离阈值
        self.max_price_deviation = config.get("execution.order_tracking.price_deviation_threshold", 0.05)  # 5%
        
        # 港股手数默认值
        self.hk_default_lot_size = 100
        
        # 最小交易金额
        self.min_trade_value = config.get("execution.min_trade_value", 200)
        
        self.logger.info("订单验证器初始化完成")
    
    async def validate_order(
        self, 
        symbol: str, 
        side: str, 
        quantity: int, 
        price: float,
        realtime_mgr=None
    ) -> Tuple[bool, str, dict]:
        """
        验证订单是否可以提交
        
        Args:
            symbol: 股票代码
            side: 交易方向 ('Buy' 或 'Sell')
            quantity: 交易数量
            price: 交易价格
            realtime_mgr: 实时数据管理器（用于获取当前价格）
            
        Returns:
            Tuple[bool, str, dict]: (是否通过验证, 消息, 详细信息)
        """
        details = {
            'symbol': symbol,
            'side': side,
            'quantity': quantity,
            'price': price,
            'checks': {}
        }
        
        try:
            # 1. 检查市场交易时间
            is_open, market_msg = self._check_market_hours(symbol)
            details['checks']['market_hours'] = {'passed': is_open, 'message': market_msg}
            if not is_open:
                return False, f"市场未开放: {market_msg}", details
            
            # 2. 检查交易金额是否达到最小值
            trade_value = float(price) * int(quantity)
            if trade_value < self.min_trade_value:
                msg = f"交易金额过小: ${trade_value:.2f} < ${self.min_trade_value}"
                details['checks']['min_trade_value'] = {'passed': False, 'message': msg}
                return False, msg, details
            details['checks']['min_trade_value'] = {'passed': True, 'message': f"交易金额: ${trade_value:.2f}"}
            
            # 3. 检查最小交易单位
            lot_valid, lot_msg = self._check_lot_size(symbol, quantity)
            details['checks']['lot_size'] = {'passed': lot_valid, 'message': lot_msg}
            if not lot_valid:
                return False, lot_msg, details
            
            # 4. 根据交易方向进行特定检查
            if side.upper() == 'SELL':
                pos_valid, pos_msg = self._check_position_for_sell(symbol, quantity)
                details['checks']['position'] = {'passed': pos_valid, 'message': pos_msg}
                if not pos_valid:
                    return False, pos_msg, details
            elif side.upper() == 'SHORT':
                fund_valid, fund_msg = self._check_funds_for_buy(symbol, quantity, price)
                details['checks']['funds'] = {'passed': fund_valid, 'message': f"做空保证金检查: {fund_msg}"}
                if not fund_valid:
                    return False, f"做空资金不足: {fund_msg}", details
            else:  # BUY / COVER
                fund_valid, fund_msg = self._check_funds_for_buy(symbol, quantity, price)
                details['checks']['funds'] = {'passed': fund_valid, 'message': fund_msg}
                if not fund_valid:
                    return False, fund_msg, details
            
            # 5. 检查价格合理性（如果有实时数据管理器）
            if realtime_mgr:
                price_valid, price_msg = await self._check_price_reasonability(
                    symbol, price, realtime_mgr
                )
                details['checks']['price'] = {'passed': price_valid, 'message': price_msg}
                if not price_valid:
                    return False, price_msg, details
            else:
                details['checks']['price'] = {'passed': True, 'message': '无实时数据，跳过价格检查'}
            
            # 6. 检查日内订单限制
            order_limit_valid, order_limit_msg = self._check_daily_order_limit()
            details['checks']['daily_limit'] = {'passed': order_limit_valid, 'message': order_limit_msg}
            if not order_limit_valid:
                return False, order_limit_msg, details
            
            # 所有检查通过
            return True, "订单验证通过", details
            
        except Exception as e:
            self.logger.error(f"订单验证过程发生错误: {e}")
            return False, f"验证过程异常: {str(e)}", details
    
    def _check_market_hours(self, symbol: str) -> Tuple[bool, str]:
        """
        检查市场交易时间
        
        Args:
            symbol: 股票代码
            
        Returns:
            Tuple[bool, str]: (是否在交易时间, 消息)
        """
        try:
            now = datetime.now()
            
            if symbol.endswith('.HK'):
                # 港股交易时间 (香港时区)
                hk_tz = pytz.timezone('Asia/Hong_Kong')
                hk_time = datetime.now(hk_tz)
                
                # 周末休市
                if hk_time.weekday() >= 5:
                    return False, f"周末休市 (星期{hk_time.weekday() + 1})"
                
                hour = hk_time.hour
                minute = hk_time.minute
                current_time = hour * 60 + minute
                
                # 港股交易时段
                # 早盘: 9:30 - 12:00
                # 午盘: 13:00 - 16:00
                morning_open = 9 * 60 + 30   # 9:30
                morning_close = 12 * 60      # 12:00
                afternoon_open = 13 * 60     # 13:00
                afternoon_close = 16 * 60    # 16:00
                
                if morning_open <= current_time <= morning_close:
                    return True, f"港股早盘交易时段 ({hk_time.strftime('%H:%M')})"
                elif afternoon_open <= current_time <= afternoon_close:
                    return True, f"港股午盘交易时段 ({hk_time.strftime('%H:%M')})"
                else:
                    return False, f"非港股交易时段 ({hk_time.strftime('%H:%M')})"
                
            elif symbol.endswith('.US'):
                # 美股交易时间 (纽约时区)
                us_tz = pytz.timezone('America/New_York')
                us_time = datetime.now(us_tz)
                
                # 周末休市
                if us_time.weekday() >= 5:
                    return False, f"周末休市 (星期{us_time.weekday() + 1})"
                
                hour = us_time.hour
                minute = us_time.minute
                current_time = hour * 60 + minute
                
                # 美股交易时段: 9:30 - 16:00
                market_open = 9 * 60 + 30   # 9:30
                market_close = 16 * 60      # 16:00
                
                if market_open <= current_time <= market_close:
                    return True, f"美股交易时段 ({us_time.strftime('%H:%M')})"
                else:
                    # 盘前盘后交易（4:00-9:30, 16:00-20:00）可能也允许
                    pre_market_open = 4 * 60
                    after_market_close = 20 * 60
                    
                    if pre_market_open <= current_time < market_open:
                        return True, f"美股盘前交易 ({us_time.strftime('%H:%M')})"
                    elif market_close < current_time <= after_market_close:
                        return True, f"美股盘后交易 ({us_time.strftime('%H:%M')})"
                    else:
                        return False, f"非美股交易时段 ({us_time.strftime('%H:%M')})"
            
            # 其他市场默认允许
            return True, "未知市场，假设可交易"
            
        except Exception as e:
            self.logger.warning(f"检查市场时间失败: {e}，默认允许交易")
            return True, f"市场时间检查异常，默认允许: {str(e)}"
    
    def _check_lot_size(self, symbol: str, quantity: int) -> Tuple[bool, str]:
        """
        检查交易数量是否符合最小交易单位要求
        
        Args:
            symbol: 股票代码
            quantity: 交易数量
            
        Returns:
            Tuple[bool, str]: (是否符合要求, 消息)
        """
        try:
            lot_size = self.order_manager.get_lot_size(symbol)
            
            if lot_size <= 0:
                lot_size = 1 if symbol.endswith('.US') else self.hk_default_lot_size
            
            if quantity <= 0:
                return False, f"交易数量必须大于0: {quantity}"
            
            if symbol.endswith('.US'):
                # 美股支持碎股，只需要数量>0
                return True, f"美股数量有效: {quantity}"
            
            # 港股等市场需要检查手数，自动调整到最近的合规手数
            if quantity % lot_size != 0:
                adjusted = (quantity // lot_size) * lot_size
                if adjusted == 0:
                    adjusted = lot_size
                self.logger.info(
                    f"自动调整 {symbol} 数量: {quantity} -> {adjusted} "
                    f"(每手{lot_size}股)"
                )
                return True, f"数量已自动调整为{adjusted}(每手{lot_size}股)"
            
            return True, f"数量{quantity}符合手数要求(每手{lot_size}股)"
            
        except Exception as e:
            self.logger.warning(f"检查手数失败: {e}")
            return True, f"手数检查异常，默认通过: {str(e)}"
    
    def _check_position_for_sell(self, symbol: str, quantity: int) -> Tuple[bool, str]:
        """
        检查卖出时持仓是否足够
        
        Args:
            symbol: 股票代码
            quantity: 卖出数量
            
        Returns:
            Tuple[bool, str]: (是否可以卖出, 消息)
        """
        try:
            positions = self.order_manager.get_positions(symbol)
            
            if not positions:
                return False, f"没有{symbol}的持仓，无法卖出"
            
            # 查找匹配的持仓
            position = None
            for pos in positions:
                if getattr(pos, 'symbol', '').upper() == symbol.upper():
                    position = pos
                    break
            
            if position is None:
                return False, f"没有{symbol}的持仓，无法卖出"
            
            total_qty = int(getattr(position, 'quantity', 0))
            available_qty = int(getattr(position, 'available_quantity', total_qty))
            
            if total_qty <= 0:
                return False, f"{symbol}持仓数量为0或负数: {total_qty}"
            
            if quantity > available_qty:
                return False, f"可卖出数量不足: 需要{quantity}, 可用{available_qty}"
            
            return True, f"持仓足够: 需要{quantity}, 可用{available_qty}/{total_qty}"
            
        except Exception as e:
            self.logger.warning(f"检查持仓失败: {e}")
            return False, f"持仓检查异常: {str(e)}"
    
    def _check_funds_for_buy(self, symbol: str, quantity: int, price: float) -> Tuple[bool, str]:
        """
        检查买入时资金是否足够
        
        Args:
            symbol: 股票代码
            quantity: 买入数量
            price: 买入价格
            
        Returns:
            Tuple[bool, str]: (是否有足够资金, 消息)
        """
        try:
            balance = self.order_manager.get_account_balance()
            
            # 计算所需资金（含预估手续费）
            commission_rate = self.config.get('execution.default_commission_rate', 0.0025)
            required_amount = float(price) * int(quantity) * (1 + commission_rate)
            
            # 预留一定缓冲
            buffer_pct = 0.02  # 2% 缓冲
            required_with_buffer = required_amount * (1 + buffer_pct)
            
            if balance <= 0:
                return False, f"账户余额为负或零: ${balance:.2f}"
            
            if balance < required_with_buffer:
                return False, f"资金不足: 需要${required_with_buffer:.2f}, 可用${balance:.2f}"
            
            remaining = balance - required_with_buffer
            return True, f"资金充足: 需要${required_with_buffer:.2f}, 可用${balance:.2f}, 剩余${remaining:.2f}"
            
        except Exception as e:
            self.logger.warning(f"检查资金失败: {e}")
            return False, f"资金检查异常: {str(e)}"
    
    async def _check_price_reasonability(
        self, 
        symbol: str, 
        order_price: float, 
        realtime_mgr
    ) -> Tuple[bool, str]:
        """
        检查订单价格是否合理
        
        Args:
            symbol: 股票代码
            order_price: 订单价格
            realtime_mgr: 实时数据管理器
            
        Returns:
            Tuple[bool, str]: (价格是否合理, 消息)
        """
        try:
            # 获取当前价格
            quotes = await realtime_mgr.get_quote([symbol])
            
            if not quotes or symbol not in quotes:
                return True, "无法获取实时价格，跳过价格检查"
            
            current_price = float(quotes[symbol].last_done)
            
            if current_price <= 0:
                return True, "当前价格无效，跳过价格检查"
            
            # 计算价格偏离
            deviation = abs(order_price - current_price) / current_price
            
            if deviation > self.max_price_deviation:
                return False, f"价格偏离过大: 订单价${order_price:.2f}, 市价${current_price:.2f}, 偏离{deviation:.1%} > {self.max_price_deviation:.0%}"
            
            return True, f"价格合理: 订单价${order_price:.2f}, 市价${current_price:.2f}, 偏离{deviation:.1%}"
            
        except Exception as e:
            self.logger.warning(f"检查价格失败: {e}")
            return True, f"价格检查异常，默认通过: {str(e)}"
    
    def _check_daily_order_limit(self) -> Tuple[bool, str]:
        """
        检查日内订单数量限制
        
        Returns:
            Tuple[bool, str]: (是否可以继续下单, 消息)
        """
        try:
            daily_count = getattr(self.order_manager, 'daily_order_count', 0)
            max_daily = getattr(self.order_manager, 'max_daily_orders', 200)
            
            if daily_count >= max_daily:
                return False, f"已达日内订单上限: {daily_count}/{max_daily}"
            
            remaining = max_daily - daily_count
            return True, f"日内订单: {daily_count}/{max_daily}, 剩余{remaining}笔"
            
        except Exception as e:
            self.logger.warning(f"检查日内订单限制失败: {e}")
            return True, f"订单限制检查异常，默认通过: {str(e)}"
    
    def validate_order_sync(
        self, 
        symbol: str, 
        side: str, 
        quantity: int, 
        price: float
    ) -> Tuple[bool, str]:
        """
        同步版本的订单验证（不检查价格合理性）
        
        Args:
            symbol: 股票代码
            side: 交易方向 ('Buy' 或 'Sell')
            quantity: 交易数量
            price: 交易价格
            
        Returns:
            Tuple[bool, str]: (是否通过验证, 消息)
        """
        try:
            # 1. 检查市场交易时间
            is_open, market_msg = self._check_market_hours(symbol)
            if not is_open:
                return False, f"市场未开放: {market_msg}"
            
            # 2. 检查交易金额
            trade_value = float(price) * int(quantity)
            if trade_value < self.min_trade_value:
                return False, f"交易金额过小: ${trade_value:.2f} < ${self.min_trade_value}"
            
            # 3. 检查最小交易单位
            lot_valid, lot_msg = self._check_lot_size(symbol, quantity)
            if not lot_valid:
                return False, lot_msg
            
            # 4. 根据交易方向检查
            if side.upper() == 'SELL':
                pos_valid, pos_msg = self._check_position_for_sell(symbol, quantity)
                if not pos_valid:
                    return False, pos_msg
            elif side.upper() == 'SHORT':
                fund_valid, fund_msg = self._check_funds_for_buy(symbol, quantity, price)
                if not fund_valid:
                    return False, f"做空资金不足: {fund_msg}"
            else:
                fund_valid, fund_msg = self._check_funds_for_buy(symbol, quantity, price)
                if not fund_valid:
                    return False, fund_msg
            
            # 5. 检查日内订单限制
            limit_valid, limit_msg = self._check_daily_order_limit()
            if not limit_valid:
                return False, limit_msg
            
            return True, "订单验证通过"
            
        except Exception as e:
            self.logger.error(f"订单验证异常: {e}")
            return False, f"验证异常: {str(e)}"
    
    def get_validation_summary(self, details: dict) -> str:
        """
        生成验证结果摘要
        
        Args:
            details: 验证详情字典
            
        Returns:
            str: 格式化的摘要字符串
        """
        lines = [
            f"📋 订单验证结果: {details.get('symbol', 'N/A')}",
            f"   方向: {details.get('side', 'N/A')}, 数量: {details.get('quantity', 0)}, 价格: ${details.get('price', 0):.2f}"
        ]
        
        checks = details.get('checks', {})
        for check_name, check_result in checks.items():
            status = "✅" if check_result.get('passed', False) else "❌"
            message = check_result.get('message', '')
            lines.append(f"   {status} {check_name}: {message}")
        
        return "\n".join(lines)
