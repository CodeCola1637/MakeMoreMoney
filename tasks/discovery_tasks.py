"""Discovery and institutional tracking tasks extracted from main.py."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from strategy.signals import Signal, SignalType

if TYPE_CHECKING:
    from tasks.trading_context import TradingContext

logger = logging.getLogger("tasks.discovery")


async def stock_discovery_task(ctx: TradingContext):
    """股票发现任务（单次执行）"""
    if not ctx.stock_discovery:
        return

    try:
        ready_stocks = await ctx.stock_discovery.run_discovery_cycle()

        logger.info(ctx.stock_discovery.get_watch_list_summary())

        auto_trade = ctx.config.get("discovery.auto_trade", False)
        if ready_stocks and auto_trade:
            existing_symbols = set()
            try:
                positions = ctx.order_mgr.get_positions()
                existing_symbols = {
                    getattr(p, "symbol", "").upper() for p in positions
                }
            except Exception:
                pass

            for candidate in ready_stocks:
                if candidate.symbol.upper() in existing_symbols:
                    logger.debug(f"发现模块跳过已持仓: {candidate.symbol}")
                    continue
                try:
                    lot_size = ctx.order_mgr.get_lot_size(candidate.symbol)
                    min_trade_val = ctx.config.get(
                        "execution.min_trade_value", 200
                    )
                    quantity = max(
                        lot_size,
                        (
                            int(min_trade_val / candidate.entry_price)
                            if candidate.entry_price > 0
                            else lot_size
                        ),
                    )
                    quantity = (quantity // lot_size) * lot_size
                    if quantity <= 0:
                        quantity = lot_size

                    signal = Signal(
                        symbol=candidate.symbol,
                        signal_type=SignalType.BUY,
                        price=candidate.entry_price,
                        quantity=quantity,
                        confidence=candidate.confidence,
                        strategy_name="discovery",
                    )
                    await ctx.on_signal(signal)
                    logger.info(
                        f"🎯 发现模块生成买入信号: {candidate.symbol}, 数量: {quantity}"
                    )
                except Exception as e:
                    logger.error(
                        f"生成发现信号失败: {candidate.symbol}, {e}"
                    )
        elif ready_stocks:
            for candidate in ready_stocks:
                logger.info(
                    f"💡 发现入场机会（未启用自动交易）: {candidate.symbol} "
                    f"@ {candidate.current_price:.2f}, "
                    f"原因: {candidate.discovery_reason.value}"
                )

    except Exception as e:
        logger.error(f"股票发现任务错误: {e}")


async def institutional_tracking_task(ctx: TradingContext):
    """机构交易跟踪任务 — 扫描 SEC 数据并更新策略缓存"""
    if not ctx.institutional_tracker:
        return

    try:
        signals = await ctx.institutional_tracker.run_scan_cycle(
            watch_symbols=ctx.symbols
        )

        logger.info(ctx.institutional_tracker.get_summary())

        if signals:
            ctx.sec_strategy.update_signals(signals)
            for inst_sig in signals:
                logger.info(
                    f"🏦 SEC 信号已缓存: {inst_sig.symbol} "
                    f"{inst_sig.signal_type}, 置信度={inst_sig.confidence:.2f}, "
                    f"{inst_sig.reason}"
                )

    except Exception as e:
        logger.error(f"机构交易跟踪任务错误: {e}")
