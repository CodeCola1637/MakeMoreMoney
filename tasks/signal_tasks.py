"""Signal generation tasks extracted from main.py."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tasks.trading_context import TradingContext

logger = logging.getLogger("tasks.signal")


async def ensemble_signal_generation(ctx: TradingContext):
    """策略组合器信号生成任务（单次执行）"""
    start_time = datetime.now()
    signals_generated = 0

    all_quotes = await ctx.realtime_mgr.get_quote(ctx.symbols)

    for symbol in ctx.symbols:
        try:
            if not (all_quotes and symbol in all_quotes):
                logger.warning(f"无法获取 {symbol} 的最新行情数据")
                continue

            market_data = {
                "last_done": float(all_quotes[symbol].last_done),
                "timestamp": datetime.now().isoformat(),
            }

            ensemble_signal = await ctx.signal_gen.generate_ensemble_signal(
                symbol, market_data
            )

            if ensemble_signal and ensemble_signal.signal_type.value != "HOLD":
                logger.info(
                    f"📊 组合信号: {symbol} - {ensemble_signal.signal_type.value}, "
                    f"置信度: {ensemble_signal.confidence:.3f}"
                )
                await ctx.on_signal(ensemble_signal)
                signals_generated += 1

        except Exception as e:
            logger.error(f"处理组合信号失败: {symbol}, 错误: {e}")

    duration = (datetime.now() - start_time).total_seconds()
    logger.info(
        f"✅ 信号生成完成，耗时: {duration:.2f}秒, 生成信号: {signals_generated}个"
    )


async def single_strategy_signal_generation(ctx: TradingContext):
    """单一策略信号生成任务（单次执行）"""
    await ctx.signal_gen.generate_all_signals()


async def volume_anomaly_task(ctx: TradingContext):
    """异常成交量检测 — 更新策略缓存并立即触发 ensemble 投票"""
    if not ctx.volume_detector:
        return

    try:
        vol_signals = await ctx.volume_detector.check_and_generate_signals()

        if not vol_signals:
            return

        logger.info(ctx.volume_detector.get_summary())

        triggered_symbols = ctx.volume_strategy.update_signals(vol_signals)

        if ctx.ensemble_enabled and triggered_symbols:
            all_quotes = await ctx.realtime_mgr.get_quote(triggered_symbols)

            for symbol in triggered_symbols:
                try:
                    if not (all_quotes and symbol in all_quotes):
                        logger.warning(f"即时评估: 无法获取 {symbol} 行情，跳过")
                        continue

                    market_data = {
                        "last_done": float(all_quotes[symbol].last_done),
                        "timestamp": datetime.now().isoformat(),
                    }

                    ensemble_signal = await ctx.signal_gen.generate_ensemble_signal(
                        symbol, market_data
                    )

                    if (
                        ensemble_signal
                        and ensemble_signal.signal_type.value != "HOLD"
                    ):
                        logger.info(
                            f"⚡ 即时 Ensemble 投票结果: {symbol} "
                            f"{ensemble_signal.signal_type.value}, "
                            f"置信度={ensemble_signal.confidence:.3f}, "
                            f"策略={ensemble_signal.extra_data.get('contributing_strategies', [])}"
                        )
                        await ctx.on_signal(ensemble_signal)
                    else:
                        logger.info(
                            f"📊 即时 Ensemble: {symbol} 综合判定 HOLD, 暂不交易"
                        )
                except Exception as e:
                    logger.error(f"即时 ensemble 评估失败 {symbol}: {e}")

    except Exception as e:
        logger.error(f"异常成交量检测任务错误: {e}")
