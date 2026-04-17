"""Order submission, pricing, lot sizes, and cost analysis extracted from OrderManager."""

import re
import traceback
import decimal
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, Tuple

from longport.openapi import (
    OrderSide, OrderType, OrderStatus, TimeInForceType
)


class OrderExecutor:
    """Handles order submission, pricing rules, lot size management, and cost analysis."""

    def __init__(self, manager):
        self._mgr = manager

    @property
    def logger(self):
        return self._mgr.logger

    @property
    def trade_ctx(self):
        return self._mgr.trade_ctx

    @property
    def config(self):
        return self._mgr.config

    # ── Lot size helpers ──

    _lot_size_cache: Dict[str, int] = {}

    def _query_lot_size_from_api(self, symbol: str) -> Optional[int]:
        """Query lot size from LongPort QuoteContext.static_info API."""
        try:
            from longport.openapi import QuoteContext as QCtx
            quote_ctx = QCtx(self._mgr.longport_config)
            infos = quote_ctx.static_info([symbol])
            if infos:
                info = infos[0] if isinstance(infos, list) else infos
                lot = int(getattr(info, 'lot_size', 0))
                if lot > 0:
                    self.logger.info(f"从API获取 {symbol} 手数: {lot}")
                    return lot
        except Exception as e:
            self.logger.warning(f"API查询 {symbol} 手数失败: {e}")
        return None

    def _get_real_lot_size(self, symbol: str) -> int:
        """获取真实的港股手数信息（缓存 + API查询 + 配置兜底）"""
        if symbol in self._lot_size_cache:
            return self._lot_size_cache[symbol]

        lot = self._query_lot_size_from_api(symbol)
        if lot:
            self._lot_size_cache[symbol] = lot
            return lot

        configured_lots = self.config.get("execution.lot_sizes", {})
        lot = configured_lots.get(symbol, 100)
        self._lot_size_cache[symbol] = lot
        self.logger.warning(f"使用兜底手数 {symbol}: {lot}")
        return lot

    def get_lot_size(self, symbol: str) -> int:
        """获取股票的最小交易单位"""
        try:
            if '.HK' in symbol:
                lot_size = self._get_real_lot_size(symbol)
            elif '.US' in symbol:
                lot_size = 1
            elif '.SH' in symbol or '.SZ' in symbol:
                lot_size = 100
            else:
                lot_size = 100

            if symbol not in self._mgr.min_quantity_unit:
                self._mgr.min_quantity_unit[symbol] = lot_size

            return lot_size
        except Exception as e:
            self.logger.error(f"获取股票手数出错: {e}")
            return 100

    def _adjust_lot_size(self, symbol: str, quantity: int) -> int:
        """调整股票手数，确保符合最小交易单位"""
        if not self.trade_ctx:
            try:
                if hasattr(self._mgr, 'initialize'):
                    self._mgr.initialize()
            except Exception as e:
                self.logger.error(f"初始化交易上下文失败: {e}")

        try:
            lot_size = self.get_lot_size(symbol)
            self.logger.debug(f"{symbol} 获取到手数: {lot_size}")

            if '.US' in symbol:
                if quantity < 1:
                    self.logger.info("美股交易数量小于1股，调整为最小交易单位: 1股")
                    return 1
                return quantity

            if lot_size > 0:
                adjusted_quantity = (quantity // lot_size) * lot_size
                if adjusted_quantity == 0 and quantity > 0:
                    adjusted_quantity = lot_size

                if adjusted_quantity != quantity:
                    self.logger.info(f"调整交易数量以符合最小交易单位: {quantity} -> {adjusted_quantity}")

                return adjusted_quantity
            else:
                self.logger.warning(f"无法获取正确的手数信息，使用原始数量: {quantity}")
                return quantity
        except Exception as e:
            self.logger.warning(f"调整交易数量出错: {e}，使用原始数量: {quantity}")
            return quantity

    # ── Price tick helpers ──

    def _get_hk_price_tick(self, price) -> float:
        """获取港股价格的最小变动单位"""
        try:
            price_float = float(price) if price else 0.0

            if price_float <= 0.25:
                return 0.001
            elif price_float <= 0.50:
                return 0.005
            elif price_float <= 10.00:
                return 0.01
            elif price_float <= 20.00:
                return 0.02
            elif price_float <= 100.00:
                return 0.05
            elif price_float <= 200.00:
                return 0.10
            elif price_float <= 500.00:
                return 0.20
            else:
                return 0.50
        except (ValueError, TypeError) as e:
            self.logger.error(f"获取港股价格精度时出错: {e}, 使用默认精度0.05")
            return 0.05

    def _adjust_price_to_tick(self, symbol: str, price) -> float:
        """调整价格到符合最小变动单位的价格"""
        try:
            if isinstance(price, Decimal):
                price_float = float(price)
            elif isinstance(price, (int, float)):
                price_float = float(price)
            else:
                price_float = float(str(price))

            if ".HK" in symbol:
                tick = self._get_hk_price_tick(price_float)
                adjusted_price = round(price_float / tick) * tick
                if abs(adjusted_price - price_float) > 0.001:
                    self.logger.info(f"调整港股价格 {symbol}: {price_float} -> {adjusted_price} (精度: {tick})")
                return round(adjusted_price, 3)
            elif ".US" in symbol:
                return round(price_float, 2)
            else:
                return round(price_float, 2)
        except (ValueError, TypeError, decimal.InvalidOperation) as e:
            self.logger.error(f"调整价格精度时出错: {e}, 使用原始价格: {price}")
            try:
                return float(price) if price else 0.0
            except:
                return 0.0

    def _validate_order_parameters(self, symbol: str, price, quantity: int) -> Tuple[bool, str, float, int]:
        """验证并自动修正订单参数，使其符合交易所规则"""
        try:
            if isinstance(price, Decimal):
                price_float = float(price)
            elif isinstance(price, (int, float)):
                price_float = float(price)
            else:
                price_float = float(str(price))

            corrected_price = price_float
            corrected_quantity = quantity

            if price_float <= 0:
                return False, "价格必须大于0", price_float, quantity

            if quantity <= 0:
                return False, "数量必须大于0", price_float, quantity

            if ".HK" in symbol:
                tick = self._get_hk_price_tick(price_float)

                try:
                    from decimal import Decimal as D, ROUND_HALF_UP
                    price_decimal = D(str(price_float))
                    tick_decimal = D(str(tick))
                    remainder = price_decimal % tick_decimal

                    if remainder != D('0'):
                        adjusted = (price_decimal / tick_decimal).quantize(D('1'), rounding=ROUND_HALF_UP) * tick_decimal
                        corrected_price = float(adjusted)
                        self.logger.info(f"自动修正港股价格: {price_float} -> {corrected_price} (tick={tick})")
                except Exception as e:
                    self.logger.warning(f"Decimal计算失败，使用浮点数逻辑: {e}")
                    remainder = price_float % tick
                    if abs(remainder) > 0.0001 and abs(remainder - tick) > 0.0001:
                        corrected_price = round(round(price_float / tick) * tick, 3)
                        self.logger.info(f"自动修正港股价格: {price_float} -> {corrected_price} (tick={tick})")

                lot_size = self.get_lot_size(symbol)
                if corrected_quantity % lot_size != 0:
                    corrected_quantity = max(lot_size, (corrected_quantity // lot_size) * lot_size)
                    self.logger.info(f"自动修正港股数量: {quantity} -> {corrected_quantity} (lot_size={lot_size})")
                    if corrected_quantity <= 0:
                        return False, f"数量{quantity}调整后为0，最小交易手数{lot_size}", price_float, quantity

            elif ".US" in symbol:
                if round(price_float, 2) != price_float:
                    corrected_price = round(price_float, 2)
                    self.logger.info(f"自动修正美股价格精度: {price_float} -> {corrected_price}")

            return True, "", corrected_price, corrected_quantity

        except Exception as e:
            error_msg = f"验证订单参数时发生错误: {e}"
            self.logger.error(error_msg)
            return False, error_msg, float(price) if price else 0.0, quantity

    # ── Core submission ──

    async def _submit_order(self, symbol: str, price: float, quantity: int, order_type: str, strategy_name: str,
                            signal_source: str = "", signal_confidence: float = 0.0):
        """提交订单
        
        signal_source / signal_confidence 用于事后归因：
        - signal_source: 触发源策略名（如 'volume_anomaly,ccass'）
        - signal_confidence: 信号置信度（0~1）
        """
        from execution.order_manager import OrderResult
        order_type = order_type.lower()
        try:
            pending_count = len([order for order in self._mgr.active_orders.values()
                               if not order.is_filled() and not order.is_canceled() and not order.is_rejected()])

            if pending_count >= self._mgr.max_pending_orders:
                self.logger.warning(f"达到最大挂单数量限制: {pending_count}/{self._mgr.max_pending_orders}")

                confidence = 0.05
                if isinstance(strategy_name, str) and 'confidence' in strategy_name.lower():
                    try:
                        confidence_match = re.search(r'confidence[=:]?\s*([0-9.]+)', strategy_name.lower())
                        if confidence_match:
                            confidence = float(confidence_match.group(1))
                    except:
                        pass

                if confidence > 0.10:
                    self.logger.info(f"高置信度信号({confidence:.2%})转为市价单执行: {symbol}")
                    await self._mgr.order_tracker._cleanup_one_low_quality_order()

                    pending_count = len([order for order in self._mgr.active_orders.values()
                                       if not order.is_filled() and not order.is_canceled() and not order.is_rejected()])

                    if pending_count >= self._mgr.max_pending_orders:
                        self.logger.info(f"转为市价单执行: {symbol}, 置信度: {confidence:.2%}")
                else:
                    rej = OrderResult(
                        order_id="",
                        symbol=symbol,
                        side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                        quantity=quantity,
                        price=price,
                        status=OrderStatus.Rejected,
                        submitted_at=datetime.now(),
                        msg=f"挂单限制且置信度低({confidence:.2%})，拒绝执行",
                        strategy_name=strategy_name,
                        signal_source=signal_source,
                        signal_confidence=signal_confidence,
                    )
                    rej.reject_reason = "pending_limit_low_confidence"
                    return rej

            adjusted_price = self._adjust_price_to_tick(symbol, price)

            if order_type == "buy":
                confidence = 0.1
                is_effective, cost_reason = self._is_trade_cost_effective(symbol, quantity, adjusted_price, confidence)

                if not is_effective:
                    self.logger.warning(f"交易被成本效益分析拒绝: {cost_reason}")

                    optimized_quantity = self._optimize_trade_size(symbol, quantity, adjusted_price, confidence)

                    if optimized_quantity > 0 and optimized_quantity != quantity:
                        self.logger.info(f"使用优化后的交易数量: {quantity} -> {optimized_quantity}")
                        quantity = optimized_quantity
                    elif optimized_quantity == 0:
                        rej = OrderResult(
                            order_id="",
                            symbol=symbol,
                            side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                            quantity=quantity,
                            price=adjusted_price,
                            status=OrderStatus.Rejected,
                            submitted_at=datetime.now(),
                            msg=f"成本效益分析失败: {cost_reason}",
                            strategy_name=strategy_name,
                            signal_source=signal_source,
                            signal_confidence=signal_confidence,
                        )
                        rej.reject_reason = "cost_benefit_failed"
                        return rej

            self.logger.info(f"准备提交订单 - 股票: {symbol}, 价格: {adjusted_price}, 数量: {quantity}, 类型: {order_type}, 策略: {strategy_name}")

            adjusted_quantity = self._adjust_lot_size(symbol, quantity)
            self.logger.info(f"数量调整: {symbol} {quantity} -> {adjusted_quantity}")

            order_resp = self.trade_ctx.submit_order(
                symbol=symbol,
                order_type=OrderType.LO,
                side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                submitted_quantity=quantity,
                time_in_force=TimeInForceType.Day,
                submitted_price=Decimal(str(adjusted_price)),
                remark=f"策略订单-{strategy_name}"
            )

            self.logger.info(f"订单提交成功: {symbol}, 订单ID: {order_resp.order_id}")

            self._mgr.daily_order_count += 1

            import time
            result = OrderResult(
                order_id=order_resp.order_id,
                symbol=symbol,
                side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                quantity=quantity,
                price=adjusted_price,
                status=OrderStatus.NotReported,
                submitted_at=datetime.now(),
                msg="",
                strategy_name=strategy_name,
                signal_source=signal_source,
                signal_confidence=signal_confidence,
            )

            self._mgr.active_orders[order_resp.order_id] = result
            self._mgr.order_update_time[order_resp.order_id] = time.time()

            self._mgr.order_tracker._save_order_to_csv(result)

            self.logger.info(f"订单提交完成: {result}")
            return result

        except Exception as e:
            error_str = str(e)
            self.logger.error(f"提交订单到交易所失败: {symbol}, 错误: {error_str}")

            if "603301" in error_str or "not support short selling" in error_str.lower():
                self._mgr._short_blacklist.add(symbol.upper())
                self.logger.warning(f"已将 {symbol} 加入做空黑名单 (603301)")

            err_result = OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy if order_type == "buy" else OrderSide.Sell,
                quantity=quantity,
                price=adjusted_price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=f"提交失败: {error_str}",
                strategy_name=strategy_name,
                signal_source=signal_source,
                signal_confidence=signal_confidence,
            )
            err_result.reject_reason = self._extract_reject_reason(error_str)
            return err_result

    @staticmethod
    def _extract_reject_reason(err: str) -> str:
        """从券商错误字符串中提取标准化拒绝码/类别。"""
        if not err:
            return ""
        s = err.lower()
        if "603301" in err or "short selling" in s:
            return "short_not_supported"
        if "insufficient" in s or "buying power" in s or "余额不足" in err:
            return "insufficient_funds"
        if "market" in s and ("close" in s or "not open" in s):
            return "market_closed"
        if "price" in s and ("invalid" in s or "tick" in s):
            return "invalid_price"
        if "rejected" in s:
            return "exchange_rejected"
        return "submit_error"
    
    async def submit_buy_order(self, symbol: str, price: float, quantity: int, strategy_name: str = "default",
                                signal_source: str = "", signal_confidence: float = 0.0):
        """提交买入订单"""
        from execution.order_manager import OrderResult
        self.logger.info(f"提交买入订单: {symbol}, 价格: {price}, 数量: {quantity}, 策略: {strategy_name}")

        is_valid, error_msg, corrected_price, corrected_quantity = self._validate_order_parameters(symbol, price, quantity)
        if not is_valid:
            self.logger.error(f"订单参数验证失败: {error_msg}")
            rej = OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=error_msg,
                strategy_name=strategy_name,
                signal_source=signal_source,
                signal_confidence=signal_confidence,
            )
            rej.reject_reason = "invalid_params"
            return rej

        price = corrected_price
        quantity = corrected_quantity

        is_exit_order = strategy_name and strategy_name.startswith("profit_stop_")
        if is_exit_order:
            self.logger.info(f"止盈止损平仓订单，跳过风控现金检查: {symbol}")
        elif not await self._mgr.risk_control_check(symbol, quantity, price, is_buy=True):
            self.logger.warning(f"买入订单未通过风险控制检查: {symbol}, 价格: {price}, 数量: {quantity}")
            rej = OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Buy,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg="未通过风险控制检查",
                strategy_name=strategy_name,
                signal_source=signal_source,
                signal_confidence=signal_confidence,
            )
            rej.reject_reason = "risk_control_failed"
            return rej

        return await self._submit_order(
            symbol=symbol,
            price=price,
            quantity=quantity,
            order_type="buy",
            strategy_name=strategy_name,
            signal_source=signal_source,
            signal_confidence=signal_confidence,
        )

    async def submit_sell_order(self, symbol: str, price: float, quantity: int, strategy_name: str = "default",
                                 signal_source: str = "", signal_confidence: float = 0.0):
        """提交卖出订单"""
        from execution.order_manager import OrderResult
        self.logger.info(f"提交卖出订单: {symbol}, 价格: {price}, 数量: {quantity}, 策略: {strategy_name}")

        is_valid, error_msg, corrected_price, corrected_quantity = self._validate_order_parameters(symbol, price, quantity)
        if not is_valid:
            self.logger.error(f"订单参数验证失败: {error_msg}")
            rej = OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Sell,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg=error_msg,
                strategy_name=strategy_name,
                signal_source=signal_source,
                signal_confidence=signal_confidence,
            )
            rej.reject_reason = "invalid_params"
            return rej

        price = corrected_price
        quantity = corrected_quantity

        is_exit_order = strategy_name and strategy_name.startswith("profit_stop_")
        if is_exit_order:
            self.logger.info(f"止盈止损平仓卖出订单，跳过风控检查: {symbol}")
        elif not await self._mgr.risk_control_check(symbol, quantity, price, is_buy=False):
            self.logger.warning(f"卖出订单未通过风险控制检查: {symbol}, 价格: {price}, 数量: {quantity}")
            rej = OrderResult(
                order_id="",
                symbol=symbol,
                side=OrderSide.Sell,
                quantity=quantity,
                price=price,
                status=OrderStatus.Rejected,
                submitted_at=datetime.now(),
                msg="未通过风险控制检查",
                strategy_name=strategy_name,
                signal_source=signal_source,
                signal_confidence=signal_confidence,
            )
            rej.reject_reason = "risk_control_failed"
            return rej

        return await self._submit_order(
            symbol=symbol,
            price=price,
            quantity=quantity,
            order_type="sell",
            strategy_name=strategy_name,
            signal_source=signal_source,
            signal_confidence=signal_confidence,
        )

    async def cancel_order(self, order_id: str) -> bool:
        """取消订单（公开方法）"""
        if not self.trade_ctx:
            await self._mgr.initialize()

        if order_id not in self._mgr.active_orders:
            self.logger.warning(f"订单ID不存在: {order_id}")
            return False

        order = self._mgr.active_orders[order_id]
        if not order.is_active():
            self.logger.warning(f"订单不是活跃状态，无法取消: {order}")
            return False

        self.logger.info(f"取消订单: {order_id}")

        try:
            self.trade_ctx.cancel_order(order_id)

            order.status = OrderStatus.CancelSubmitted
            order.last_updated = datetime.now()
            order.msg = "Cancellation submitted"

            for callback in self._mgr.order_callbacks:
                try:
                    callback(order)
                except Exception as e:
                    self.logger.error(f"执行订单回调函数出错: {e}")

            return True
        except Exception as e:
            self.logger.error(f"取消订单失败: {e}")
            return False

    async def place_order(self, symbol: str, side: str, quantity: int, price_type: str = "LIMIT", price: float = None):
        """下单"""
        from execution.order_manager import OrderResult
        try:
            self.logger.info(f"下单: {symbol}, 方向: {side}, 数量: {quantity}, 类型: {price_type}")

            order_side = OrderSide.Buy if side.upper() == "BUY" else OrderSide.Sell

            is_us_stock = '.US' in symbol
            available_balance = self._mgr.position_service.get_account_balance()

            if is_us_stock and order_side == OrderSide.Buy and available_balance <= 0:
                if quantity > 10:
                    self.logger.info("美股交易: 账户余额不足，尝试减少交易数量至10股进行尝试")
                    quantity = 10

            if price_type.upper() == "MARKET":
                if price is None or price <= 0:
                    price = 100
                    self.logger.warning(f"市价单需要有效价格，使用默认价格: {price}")

                if order_side == OrderSide.Buy:
                    return await self.submit_buy_order(symbol, price, quantity, "default")
                else:
                    return await self.submit_sell_order(symbol, price, quantity, "default")
            else:
                if price is None or price <= 0:
                    self.logger.error("限价单必须指定有效价格")
                    return OrderResult(
                        order_id="",
                        symbol=symbol,
                        side=order_side,
                        quantity=quantity,
                        price=0,
                        status=OrderStatus.Rejected,
                        submitted_at=datetime.now(),
                        msg="限价单必须指定有效价格",
                        strategy_name="default"
                    )

                if order_side == OrderSide.Buy:
                    return await self.submit_buy_order(symbol, price, quantity, "default")
                else:
                    return await self.submit_sell_order(symbol, price, quantity, "default")

        except Exception as e:
            self.logger.error(f"下单失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    # ── Cost analysis helpers ──

    def _calculate_transaction_costs(self, symbol: str, price: float, quantity: int) -> Dict[str, float]:
        """计算交易总成本（包括各种手续费）"""
        try:
            is_us_stock = '.US' in symbol
            is_hk_stock = '.HK' in symbol

            transaction_value = float(price) * int(quantity)

            if is_us_stock:
                commission_rate = 0.005
                platform_fee = max(0.99, transaction_value * 0.0001)
                sec_fee = transaction_value * 0.0000051
                finra_fee = max(0.01, quantity * 0.000119)
                total_cost = commission_rate + platform_fee + sec_fee + finra_fee
            elif is_hk_stock:
                commission_rate = transaction_value * 0.0025
                stamp_duty = transaction_value * 0.001
                trading_fee = transaction_value * 0.0000565
                clearing_fee = max(2.5, transaction_value * 0.00002)
                total_cost = commission_rate + stamp_duty + trading_fee + clearing_fee
            else:
                total_cost = transaction_value * 0.003

            return {
                'transaction_value': transaction_value,
                'total_cost': total_cost,
                'cost_ratio': total_cost / transaction_value if transaction_value > 0 else 0,
                'break_even_move': total_cost * 2 / quantity
            }

        except Exception as e:
            self.logger.error(f"计算交易成本失败: {e}")
            transaction_value = float(price) * int(quantity)
            conservative_cost = transaction_value * 0.01
            return {
                'transaction_value': transaction_value,
                'total_cost': conservative_cost,
                'cost_ratio': 0.01,
                'break_even_move': conservative_cost * 2 / quantity
            }

    def _calculate_trading_costs(self, symbol: str, quantity: int, price: float) -> Dict[str, float]:
        """计算完整的交易成本"""
        try:
            trade_value = float(price) * int(quantity)

            if '.US' in symbol:
                commission_rate = float(self.config.get('us_commission_rate', 0.005))
                platform_fee = float(self.config.get('us_platform_fee', 0.99))
                sec_fee = trade_value * 0.0000278
                total_commission = max(platform_fee, trade_value * commission_rate) + sec_fee
            elif '.HK' in symbol:
                commission_rate = float(self.config.get('hk_commission_rate', 0.0025))
                stamp_duty = trade_value * 0.001
                trading_fee = trade_value * 0.00005
                clearing_fee = min(trade_value * 0.00002, 100)
                total_commission = trade_value * commission_rate + stamp_duty + trading_fee + clearing_fee
            else:
                commission_rate = float(self.config.get('default_commission_rate', 0.0025))
                total_commission = trade_value * commission_rate

            round_trip_cost = total_commission * 2
            cost_percentage = (round_trip_cost / trade_value) * 100

            return {
                'trade_value': trade_value,
                'single_commission': total_commission,
                'round_trip_cost': round_trip_cost,
                'cost_percentage': cost_percentage,
                'break_even_change': cost_percentage
            }

        except Exception as e:
            self.logger.error(f"计算交易成本失败: {e}")
            return {
                'trade_value': 0,
                'single_commission': 0,
                'round_trip_cost': 0,
                'cost_percentage': 5.0,
                'break_even_change': 5.0
            }

    def _is_trade_cost_effective(self, symbol: str, quantity: int, price: float, confidence: float) -> Tuple[bool, str]:
        """检查交易是否具有成本效益"""
        try:
            costs = self._calculate_trading_costs(symbol, quantity, price)

            min_profit_threshold = float(self.config.get('execution.min_profit_threshold', 3.0))
            max_cost_ratio = float(self.config.get('execution.max_cost_ratio', 2.0))
            min_trade_value = float(self.config.get('execution.min_trade_value', 300))

            small_trade_threshold = float(self.config.get('execution.small_trade_threshold', 500))
            small_trade_max_cost_ratio = float(self.config.get('execution.small_trade_max_cost_ratio', 3.0))

            is_small_trade = costs['trade_value'] < small_trade_threshold
            effective_max_cost_ratio = small_trade_max_cost_ratio if is_small_trade else max_cost_ratio

            if costs['trade_value'] < min_trade_value:
                return False, f"交易金额过小: ${costs['trade_value']:.0f} < ${min_trade_value:.0f}"

            if costs['cost_percentage'] > effective_max_cost_ratio:
                trade_type = "小额交易" if is_small_trade else "常规交易"
                return False, f"{trade_type}成本过高: {costs['cost_percentage']:.2f}% > {effective_max_cost_ratio}%"

            expected_return = abs(confidence) * 100
            required_return = costs['break_even_change'] + min_profit_threshold

            if expected_return < required_return:
                return False, f"预期收益不足: {expected_return:.2f}% < 需求{required_return:.2f}% (成本{costs['break_even_change']:.2f}% + 利润{min_profit_threshold}%)"

            trade_type = "小额交易" if is_small_trade else "常规交易"
            self.logger.info(f"{trade_type}成本分析通过 {symbol}: 交易额=${costs['trade_value']:.0f}, "
                           f"成本{costs['cost_percentage']:.2f}%(<{effective_max_cost_ratio}%), "
                           f"预期收益{expected_return:.2f}%(>{required_return:.2f}%)")

            return True, f"交易具有成本效益: 预期收益{expected_return:.2f}% > 成本要求{required_return:.2f}%"

        except Exception as e:
            self.logger.error(f"成本效益分析失败: {e}")
            return False, f"成本效益分析失败: {e}"

    def _optimize_trade_size(self, symbol: str, original_quantity: int, price: float, confidence: float) -> int:
        """优化交易数量以提高成本效益"""
        try:
            min_trade_value = float(self.config.get('execution.min_trade_value', 300))
            current_value = float(price) * original_quantity

            if abs(confidence) < 0.1:
                max_low_confidence_value = 500
                if current_value > max_low_confidence_value:
                    self.logger.warning(f"信号置信度过低({confidence:.1%})，限制交易金额到${max_low_confidence_value}")
                    optimized_quantity = int(max_low_confidence_value / float(price))
                    optimized_quantity = self._adjust_lot_size(symbol, optimized_quantity)
                    if optimized_quantity * float(price) < min_trade_value:
                        return 0
                    return optimized_quantity

            if current_value < min_trade_value:
                if abs(confidence) < 0.15:
                    self.logger.warning(f"信号置信度过低({confidence:.1%})，不增加交易量")
                    return 0

                optimized_quantity = max(int(min_trade_value / float(price)), 1)
                optimized_quantity = self._adjust_lot_size(symbol, optimized_quantity)

                is_effective, reason = self._is_trade_cost_effective(symbol, optimized_quantity, price, confidence)

                if is_effective:
                    self.logger.info(f"优化交易数量: {symbol} {original_quantity} -> {optimized_quantity} 股 "
                                   f"(${current_value:.0f} -> ${float(price) * optimized_quantity:.0f})")
                    return optimized_quantity
                else:
                    self.logger.warning(f"即使优化后仍不具成本效益: {reason}")
                    return 0

            is_effective, reason = self._is_trade_cost_effective(symbol, original_quantity, price, confidence)
            if not is_effective:
                self.logger.warning(f"原始交易量不具成本效益: {reason}")
                return 0

            return original_quantity

        except Exception as e:
            self.logger.error(f"优化交易数量失败: {e}")
            return 0

    def _check_profitability(self, symbol: str, price: float, quantity: int, confidence: float) -> Tuple[bool, str]:
        """检查交易的盈利能力（保留原有方法兼容性）"""
        return self._is_trade_cost_effective(symbol, quantity, price, confidence)
