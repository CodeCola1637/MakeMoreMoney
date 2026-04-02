"""Order tracking, status monitoring, persistence, and callbacks extracted from OrderManager."""

import asyncio
import csv
import os
import traceback
from datetime import datetime
from typing import Callable, Dict, List, Optional

from longport.openapi import OrderStatus


class OrderTracker:
    """Tracks active orders, persists to CSV, manages callbacks."""

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

    def register_order_callback(self, callback: Callable):
        """注册订单回调函数"""
        self._mgr.order_callbacks.append(callback)
        self.logger.debug(f"已注册订单回调函数: {callback.__name__}")

    def _notify_order_update(self, order):
        """通知订单状态更新"""
        try:
            if self._mgr.order_callbacks:
                for callback in self._mgr.order_callbacks:
                    try:
                        callback(order)
                    except Exception as e:
                        self.logger.error(f"执行订单回调时出错: {e}")
            else:
                self.logger.info(f"订单状态变化: {order.order_id}, {order.symbol}, {order.status}")
        except Exception as e:
            self.logger.error(f"通知订单更新时出错: {e}")
            self.logger.error(f"Traceback:\n{traceback.format_exc()}")

    def _start_order_tracking(self):
        """启动订单状态跟踪任务"""
        self.logger.info("启动订单状态跟踪任务...")
        self._mgr.order_tracking_task = asyncio.create_task(self._track_orders())

    async def _track_orders(self):
        """持续跟踪和更新所有活跃订单的状态"""
        self.logger.info(f"订单状态跟踪任务已启动，检查间隔: {self._mgr.order_check_interval}秒")

        while True:
            try:
                await asyncio.sleep(self._mgr.order_check_interval)

                if not self._mgr.active_orders:
                    continue

                await self._cleanup_excessive_orders()

                self.logger.info(f"检查活跃订单状态，共{len(self._mgr.active_orders)}个订单")
                current_time = datetime.now()

                orders_to_check = list(self._mgr.active_orders.items())

                for order_id, order in orders_to_check:
                    try:
                        latest_order = await self._get_order_status(order_id)

                        if not latest_order:
                            self.logger.warning(f"无法获取订单状态: {order_id}")
                            continue

                        if latest_order.status != order.status:
                            self.logger.info(f"订单状态更新: {order_id}, {order.status} -> {latest_order.status}")
                            order.status = latest_order.status

                            if order.is_filled() or order.is_canceled() or order.is_rejected():
                                self.logger.info(f"订单已完成: {order_id}, 状态: {order.status}")

                                if order.is_rejected() and order_id in self._mgr._short_sell_order_ids:
                                    sym = order.symbol.upper()
                                    if sym not in self._mgr._short_blacklist:
                                        self._mgr._short_blacklist.add(sym)
                                        self.logger.warning(f"做空订单被交易所拒绝，加入黑名单: {sym}")
                                self._mgr._short_sell_order_ids.discard(order_id)

                                del self._mgr.active_orders[order_id]
                                if order_id in self._mgr.order_update_time:
                                    del self._mgr.order_update_time[order_id]

                                self._notify_order_update(order)

                        if (not order.is_filled() and not order.is_canceled() and not order.is_rejected() and
                                order_id in self._mgr.order_update_time):
                            elapsed = (current_time - self._mgr.order_update_time[order_id]).total_seconds()

                            if elapsed > self._mgr.order_timeout:
                                self.logger.warning(f"订单超时: {order_id}, 已等待{elapsed:.1f}秒")
                                await self._cancel_order(order_id)
                    except Exception as e:
                        self.logger.error(f"处理订单{order_id}状态时出错: {e}")

            except asyncio.CancelledError:
                self.logger.info("订单状态跟踪任务被取消")
                break
            except Exception as e:
                self.logger.error(f"订单状态跟踪任务出错: {e}")
                self.logger.error(f"Traceback:\n{traceback.format_exc()}")
                await asyncio.sleep(10)

    async def _get_order_status(self, order_id: str):
        """获取订单最新状态"""
        try:
            if not self._mgr._trade_ctx_initialized:
                await self._mgr._init_trade_context()

            if not self._mgr._trade_ctx_initialized:
                self.logger.error("无法获取订单状态：交易上下文未初始化")
                return None

            order_info = self.trade_ctx.order_detail(order_id)
            if not order_info:
                self.logger.warning(f"无法获取订单详情: {order_id}")
                return None

            self._mgr.order_update_time[order_id] = datetime.now()
            return order_info
        except Exception as e:
            self.logger.error(f"获取订单状态失败: {order_id}, 错误: {e}")
            return None

    async def _handle_timeout_order(self, order):
        """处理超时订单，尝试取消并重新提交"""
        if not order or not order.order_id:
            return

        try:
            order_id = order.order_id
            self.logger.info(f"尝试取消超时订单: {order_id}")
            cancel_result = await self._cancel_order(order_id)

            if cancel_result:
                self.logger.info(f"成功取消订单: {order_id}")
                await asyncio.sleep(2)

                symbol = order.symbol
                quote = self._mgr.realtime_mgr.get_latest_quote(symbol) if hasattr(self._mgr, 'realtime_mgr') else None

                if quote:
                    latest_price = quote.last_done
                    self.logger.info(f"获取{symbol}最新价格: {latest_price}")

                    from longport.openapi import OrderSide
                    side = "buy" if order.side == OrderSide.Buy else "sell"
                    await self._mgr.order_executor._submit_order(
                        symbol=symbol,
                        price=latest_price,
                        quantity=order.quantity,
                        order_type=side,
                        strategy_name=order.strategy_name
                    )
                else:
                    self.logger.warning(f"无法获取{symbol}最新价格，未重新提交订单")
            else:
                self.logger.warning(f"取消订单失败: {order_id}")

        except Exception as e:
            self.logger.error(f"处理超时订单时出错: {e}")
            self.logger.error(f"Traceback:\n{traceback.format_exc()}")

    async def _cleanup_excessive_orders(self):
        """主动清理过多的挂单"""
        try:
            pending_orders = [
                (order_id, order) for order_id, order in self._mgr.active_orders.items()
                if not order.is_filled() and not order.is_canceled() and not order.is_rejected()
            ]

            if len(pending_orders) > self._mgr.max_pending_orders:
                pending_orders.sort(key=lambda x: x[1].submitted_at)
                orders_to_cancel = pending_orders[:len(pending_orders) - self._mgr.max_pending_orders + 1]

                self.logger.warning(f"清理过多挂单: {len(pending_orders)} -> {self._mgr.max_pending_orders}, 取消{len(orders_to_cancel)}个旧订单")

                for order_id, order in orders_to_cancel:
                    await self._cancel_order(order_id)
                    await asyncio.sleep(0.1)

        except Exception as e:
            self.logger.error(f"清理挂单时出错: {e}")

    async def _cleanup_one_low_quality_order(self):
        """清理一个低质量订单，为高质量信号让路"""
        try:
            pending_orders = [
                (order_id, order) for order_id, order in self._mgr.active_orders.items()
                if not order.is_filled() and not order.is_canceled() and not order.is_rejected()
            ]

            if not pending_orders:
                return

            pending_orders.sort(key=lambda x: x[1].submitted_at)

            if pending_orders:
                order_id, order = pending_orders[0]
                self.logger.info(f"为高质量信号让路，取消旧订单: {order_id}, {order.symbol}")
                await self._cancel_order(order_id)

        except Exception as e:
            self.logger.error(f"清理低质量订单时出错: {e}")

    async def _cancel_order(self, order_id: str) -> bool:
        """内部取消订单方法"""
        try:
            if not self._mgr._trade_ctx_initialized:
                await self._mgr._init_trade_context()

            if not self._mgr._trade_ctx_initialized:
                self.logger.error("无法取消订单：交易上下文未初始化")
                return False

            self.trade_ctx.cancel_order(order_id)

            if order_id in self._mgr.active_orders:
                self._mgr.active_orders[order_id].status = OrderStatus.CancelSubmitted
                self._notify_order_update(self._mgr.active_orders[order_id])

            return True
        except Exception as e:
            self.logger.error(f"取消订单失败: {order_id}, 错误: {e}")
            return False

    async def get_order_status(self, order_id: str):
        """获取订单状态（公开方法）"""
        if not self.trade_ctx:
            await self._mgr.initialize()

        if order_id not in self._mgr.active_orders:
            self.logger.warning(f"订单ID不存在: {order_id}")
            return None

        try:
            order_info = self.trade_ctx.order_detail(order_id)

            order = self._mgr.active_orders[order_id]
            order.update_from_order_info(order_info)

            for callback in self._mgr.order_callbacks:
                try:
                    callback(order)
                except Exception as e:
                    self.logger.error(f"执行订单回调函数出错: {e}")

            return order
        except Exception as e:
            self.logger.error(f"获取订单状态失败: {e}")
            return self._mgr.active_orders[order_id]

    async def get_today_orders(self, symbol: str = None):
        """获取今日委托"""
        try:
            orders_response = self.trade_ctx.today_orders()

            orders = orders_response.list if hasattr(orders_response, 'list') else []
            order_count = len(orders) if orders else 0
            self.logger.info(f"成功获取今日委托, 共{order_count}个")

            if symbol and orders:
                orders = [o for o in orders if o.symbol.lower() == symbol.lower()]
                self.logger.debug(f"过滤委托 {symbol}, 结果: {len(orders)}个")

            return orders
        except Exception as e:
            self.logger.error(f"获取今日委托失败: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return []

    def _check_daily_order_limit(self) -> bool:
        """检查是否达到每日订单数量限制"""
        max_daily_orders = self._mgr.max_daily_orders

        if self._mgr.daily_order_count >= max_daily_orders:
            self.logger.warning(f"已达到每日订单数量限制: {self._mgr.daily_order_count}/{max_daily_orders}")
            return False

        return True

    def _save_order(self, order_result, strategy_name: str):
        """保存订单信息（旧格式兼容）"""
        try:
            log_dir = self.config.get('logging', {}).get('dir', 'logs')
            os.makedirs(log_dir, exist_ok=True)

            orders_file = os.path.join(log_dir, 'orders.csv')
            file_exists = os.path.exists(orders_file)

            with open(orders_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'order_id', 'symbol', 'side', 'quantity', 'price',
                    'status', 'submitted_at', 'filled_at', 'cancelled_at',
                    'rejected_at', 'msg', 'strategy_name'
                ])

                if not file_exists:
                    writer.writeheader()

                writer.writerow({
                    'order_id': order_result.order_id,
                    'symbol': order_result.symbol,
                    'side': order_result.side.name if hasattr(order_result.side, 'name') else str(order_result.side),
                    'quantity': order_result.quantity,
                    'price': order_result.price,
                    'status': order_result.status.name if hasattr(order_result.status, 'name') else str(order_result.status),
                    'submitted_at': order_result.submitted_at.isoformat() if order_result.submitted_at else '',
                    'filled_at': order_result.filled_at.isoformat() if hasattr(order_result, 'filled_at') and order_result.filled_at else '',
                    'cancelled_at': order_result.cancelled_at.isoformat() if hasattr(order_result, 'cancelled_at') and order_result.cancelled_at else '',
                    'rejected_at': order_result.rejected_at.isoformat() if hasattr(order_result, 'rejected_at') and order_result.rejected_at else '',
                    'msg': order_result.msg if hasattr(order_result, 'msg') else '',
                    'strategy_name': strategy_name
                })

            self.logger.info(f"订单信息已保存到 {orders_file}")

        except Exception as e:
            self.logger.error(f"保存订单信息时发生错误: {str(e)}")
            self.logger.error(f"Traceback: {traceback.format_exc()}")

    def _save_order_to_csv(self, result):
        """保存订单信息到CSV文件"""
        try:
            os.makedirs("logs", exist_ok=True)

            csv_file = "logs/orders.csv"
            file_exists = os.path.exists(csv_file)

            with open(csv_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)

                if not file_exists:
                    writer.writerow([
                        'timestamp', 'order_id', 'symbol', 'side', 'quantity',
                        'price', 'status', 'executed_quantity'
                    ])

                side_str = str(result.side.value) if hasattr(result.side, 'value') else str(result.side)
                status_str = str(result.status.value) if hasattr(result.status, 'value') else str(result.status)
                executed_qty = getattr(result, 'executed_quantity', getattr(result, 'filled_quantity', 0))
                writer.writerow([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    result.order_id,
                    result.symbol,
                    side_str,
                    result.quantity,
                    result.price,
                    status_str,
                    executed_qty
                ])

            self.logger.info(f"订单信息已保存到 {csv_file}")

        except Exception as e:
            self.logger.error(f"保存订单信息到CSV时出错: {e}")
