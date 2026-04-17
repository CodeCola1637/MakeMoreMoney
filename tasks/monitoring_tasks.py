"""Monitoring tasks extracted from main.py."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tasks.trading_context import TradingContext

logger = logging.getLogger("tasks.monitoring")


async def portfolio_update(ctx: TradingContext):
    """投资组合状态更新（禁用自动再平衡下单）"""
    try:
        await ctx.portfolio_mgr.update_portfolio_status()
    except Exception as e:
        logger.error(f"投资组合更新错误: {e}")


async def profit_stop_monitor(ctx: TradingContext):
    """止盈止损监控任务（单次执行）"""
    try:
        positions = ctx.order_mgr.get_positions()
        position_symbols = [p.symbol for p in positions]
        all_quotes = await ctx.realtime_mgr.get_quote(position_symbols) if position_symbols else {}

        for position in positions:
            if not (all_quotes and position.symbol in all_quotes):
                continue
            current_price = float(all_quotes[position.symbol].last_done)

            cost_price = getattr(position, "cost_price", None)
            if cost_price is not None:
                cost_price = float(cost_price)
            else:
                cost_price = ctx.profit_stop_mgr.get_real_cost_price(
                    position.symbol
                )
                if cost_price is None:
                    cost_price = current_price
                    logger.warning(
                        f"⚠️ 无法获取 {position.symbol} 真实成本价"
                    )

            await ctx.profit_stop_mgr.update_position_status(
                position.symbol,
                int(position.quantity),
                cost_price,
                current_price,
            )

        active_symbols = {p.symbol for p in positions}
        stale = [
            s
            for s in list(ctx.profit_stop_mgr.position_status)
            if s not in active_symbols
        ]
        for s in stale:
            del ctx.profit_stop_mgr.position_status[s]
            ctx.profit_stop_mgr.clear_exit_pending(s)
            logger.info(f"持仓已平仓，清理跟踪状态: {s}")

        exit_signals = await ctx.profit_stop_mgr.check_exit_signals()
        for sig in exit_signals:
            success = await ctx.profit_stop_mgr.execute_exit_signal(sig)
            if success:
                logger.info(f"止盈止损订单执行成功: {sig.symbol}")
                is_stop_loss = sig.signal_type.lower() in (
                    "stop_loss",
                    "emergency_stop",
                    "trailing_stop",
                    "daily_loss_limit",
                )
                if is_stop_loss and hasattr(ctx.signal_gen, "signal_filter"):
                    await ctx.signal_gen.signal_filter.record_stop_loss_exit(
                        sig.symbol
                    )
            else:
                logger.warning(f"止盈止损订单执行失败: {sig.symbol}")

    except Exception as e:
        logger.error(f"止盈止损监控错误: {e}")


async def health_check(ctx: TradingContext):
    """系统健康检查任务（单次执行）"""
    health_report = ctx.task_manager.get_health_report()

    if not health_report["is_healthy"]:
        logger.warning(
            f"⚠️ 系统健康状态异常: 失败任务={health_report['failed_tasks']}"
        )
    else:
        logger.info(
            f"✅ 系统运行正常: 任务={health_report['total_tasks']}, "
            f"运行中={health_report['running_tasks']}"
        )

    summary = ctx.profit_stop_mgr.get_status_summary()
    if summary:
        logger.info(
            f"📊 持仓状态: 总{summary.get('total_positions', 0)}, "
            f"盈利{summary.get('profitable_positions', 0)}, "
            f"亏损{summary.get('losing_positions', 0)}"
        )

    # 保证金健康检查
    try:
        margin_status = ctx.order_mgr.fund_guard.check_margin_health()
        if margin_status["warnings"]:
            for w in margin_status["warnings"]:
                logger.warning(w)
        if "REDUCE_POSITION" in margin_status.get("actions", []):
            logger.critical(
                f"🚨 保证金危险！杠杆={margin_status['leverage']:.2f}x, "
                f"缓冲={margin_status['margin_buffer_pct']:.1f}%, "
                f"建议立即减仓"
            )
        elif not margin_status["healthy"]:
            logger.warning(
                f"⚠️ 保证金预警: 杠杆={margin_status['leverage']:.2f}x, "
                f"风险等级={margin_status['risk_level']}, 已暂停买入"
            )
        else:
            logger.debug(
                f"💰 保证金健康: 杠杆={margin_status['leverage']:.2f}x, "
                f"缓冲={margin_status['margin_buffer_pct']:.1f}%"
            )
    except Exception as e:
        logger.warning(f"保证金检查异常: {e}")
