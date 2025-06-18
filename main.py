#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import time
import logging
import asyncio
import signal
import argparse
from dotenv import load_dotenv
from longport.openapi import SubType
from datetime import datetime

from utils import ConfigLoader, setup_logger, setup_longport_env
from databases.db import init_db
from data_loader.realtime import RealtimeDataManager
from data_loader.historical import HistoricalDataLoader
from strategy.train import LSTMModelTrainer
from strategy.signals import SignalGenerator, Signal
from strategy.portfolio_manager import PortfolioManager
from strategy.profit_stop_manager import ProfitStopManager
from execution.order_manager import OrderManager, OrderResult

# 全局变量
should_continue = True
logger = None

# 创建信号处理回调函数
def create_signal_handler(order_mgr):
    """创建交易信号处理函数"""
    async def on_signal(signal_obj: Signal):
        """处理交易信号"""
        try:
            symbol = signal_obj.symbol
            signal_type = signal_obj.signal_type
            # 安全地获取signal_type的value属性
            signal_type_val = signal_type.value if hasattr(signal_type, 'value') else str(signal_type)
            price = signal_obj.price
            quantity = signal_obj.quantity
            
            # 使用安全获取的枚举值字符串进行信息记录
            logger.info(f"收到交易信号: {symbol} {signal_type_val} {quantity}股 @ {price}")
            
            # 触发订单执行
            await order_mgr.process_signal(signal_obj)
        except Exception as e:
            logger.error(f"处理信号时出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    return on_signal

# 创建订单更新回调
def create_order_update_handler():
    """创建订单状态更新处理函数"""
    def on_order_update(order_result: OrderResult):
        """处理订单状态更新"""
        logger.info(f"订单状态更新: {order_result}")
    
    return on_order_update

# 关闭处理
def shutdown():
    """处理程序关闭"""
    global should_continue
    should_continue = False
    logger.info("接收到关闭信号")

# 信号处理
def setup_signal_handlers():
    """设置信号处理器"""
    signal.signal(signal.SIGINT, lambda s, f: shutdown())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown())

async def main():
    global logger
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="量化交易系统")
    parser.add_argument("--train", action="store_true", help="训练模型")
    parser.add_argument("--symbols", nargs="+", default=["AAPL.US"], help="要交易的股票代码")
    parser.add_argument("--no-mock", action="store_true", help="禁止使用模拟数据，使用实时行情")
    args = parser.parse_args()
    
    # 加载环境变量
    load_dotenv()
    
    # 设置长桥API环境变量
    setup_longport_env()
    
    # 设置信号处理
    setup_signal_handlers()
    
    # 设置日志
    logger = setup_logger("main", "INFO")
    logger.info("设置环境变量和日志完成")
    
    # 加载配置
    config = ConfigLoader()
    
    # 设置禁用模拟数据
    if args.no_mock:
        config.update_config('quote.use_mock_data', False)
        logger.info("已禁用模拟数据，使用实时行情")
    
    # 初始化数据库
    init_db()
    
    # 初始化组件
    logger.info("初始化系统组件...")
    
    # 初始化实时数据管理器 - 使用真实API
    try:
        realtime_mgr = RealtimeDataManager(config)
        logger.info("实时数据管理器初始化完成")
    except Exception as e:
        logger.error(f"实时数据管理器初始化失败: {e}")
        return
        
    # 初始化历史数据加载器
    hist_loader = HistoricalDataLoader(config)
    logger.info("历史数据加载器初始化完成")
    
    # 初始化模型训练器
    model_trainer = LSTMModelTrainer(config, hist_loader)
    logger.info("LSTM模型训练器初始化完成")
    
    # 初始化订单管理器 - 使用真实API
    try:
        order_mgr = OrderManager(config)
        # 注入实时数据管理器，使订单管理器能够获取最新价格
        order_mgr.realtime_mgr = realtime_mgr
        logger.info("订单管理器初始化完成")
    except Exception as e:
        logger.error(f"订单管理器初始化失败: {e}")
        return
    
    # 初始化投资组合管理器
    portfolio_mgr = PortfolioManager(config, order_mgr, realtime_mgr)
    logger.info("投资组合管理器初始化完成")
    
    # 初始化止盈止损管理器
    profit_stop_mgr = ProfitStopManager(config, order_mgr)
    logger.info("止盈止损管理器初始化完成")
    
    # 初始化信号生成器（集成投资组合管理器）
    signal_gen = SignalGenerator(config, realtime_mgr, model_trainer, portfolio_mgr)
    logger.info("信号生成器初始化完成")
    
    # 创建回调处理函数
    on_signal = create_signal_handler(order_mgr)
    on_order_update = create_order_update_handler()
    
    # 如果指定了训练模式，则先训练模型
    if args.train:
        logger.info("开始训练模型...")
        for symbol in args.symbols:
            # 加载历史数据
            try:
                hist_data = await hist_loader.get_candlesticks(symbol)
                if hist_data.empty:
                    logger.warning(f"无法获取{symbol}的历史数据，跳过训练")
                    continue
                    
                # 训练模型
                await model_trainer.train_model([symbol])
                logger.info(f"{symbol}的模型训练完成")
            except Exception as e:
                logger.error(f"训练{symbol}的模型时出错: {e}")
    
    # 注册回调和启动组件
    signal_gen.register_callback(on_signal)
    order_mgr.register_order_callback(on_order_update)
    
    # 启动信号生成器（这里会预填充历史数据）
    logger.info("启动信号生成器并预填充历史数据...")
    await signal_gen.start(symbols=args.symbols)
    logger.info("信号生成器启动完成")
    
    # 启动实时数据管理器
    try:
        logger.info("正在初始化实时数据管理器...")
        await realtime_mgr.initialize()
        logger.info("实时数据管理器初始化成功")
        
        # 初始化订单管理器
        logger.info("正在初始化订单管理器...")
        await order_mgr.initialize()
        logger.info("订单管理器初始化成功")
        
        # 初始化投资组合管理器并设置目标股票（在订单管理器初始化完成后）
        logger.info("正在初始化投资组合管理器...")
        await portfolio_mgr.initialize(args.symbols)
        logger.info("投资组合管理器初始化成功")
        
        # 获取账户信息
        try:
            balance = order_mgr.get_account_balance()
            if balance:
                logger.info(f"账户余额: {balance}")
            
            positions = order_mgr.get_positions()
            if positions:
                logger.info(f"当前持仓: {positions}")
        except Exception as e:
            logger.warning(f"获取账户信息失败: {e}")
        
        # 订阅股票实时数据
        logger.info("正在订阅股票实时数据...")
        for symbol in args.symbols:
            try:
                logger.info(f"开始订阅 {symbol} 行情数据...")
                # 使用SubType.Quote枚举
                await realtime_mgr.subscribe([symbol], [SubType.Quote])
                logger.info(f"成功订阅 {symbol} 行情数据")
                
                # 获取并记录初始价格
                try:
                    logger.info(f"获取 {symbol} 初始价格...")
                    quotes = await realtime_mgr.get_quote([symbol])
                    if quotes and symbol in quotes:
                        initial_price = quotes[symbol].last_done
                        logger.info(f"初始价格 {symbol}: {initial_price}")
                    else:
                        logger.warning(f"无法获取 {symbol} 的报价数据")
                except Exception as e:
                    logger.warning(f"获取{symbol}初始价格失败: {e}")
                    
                logger.info(f"已完成 {symbol} 行情数据订阅")
            except Exception as e:
                logger.error(f"订阅 {symbol} 行情数据失败: {e}")
        
        logger.info("所有股票行情数据订阅完成！")
        
        # 启动信号生成器的定时任务
        logger.info("准备启动信号生成器的定时任务...")
        signal_interval = config.get("strategy.signal_interval", 30)  # 修改默认值为30秒
        logger.info(f"信号生成间隔设置为 {signal_interval} 秒")
        
        logger.info("创建信号生成异步任务...")
        signal_task = asyncio.create_task(signal_gen.scheduled_signal_generation(interval_seconds=signal_interval))
        logger.info("信号生成任务已创建并启动!")
        
        # 启动投资组合管理器的定期更新任务
        async def portfolio_update_task():
            """投资组合定期更新任务"""
            while should_continue:
                try:
                    await asyncio.sleep(300)  # 每5分钟更新一次投资组合状态
                    await portfolio_mgr.update_portfolio_status()
                    
                    # 检查是否需要再平衡
                    if portfolio_mgr.portfolio_status and portfolio_mgr.portfolio_status.rebalance_needed:
                        logger.info("检测到需要再平衡，开始执行...")
                        await portfolio_mgr.execute_rebalance()
                        
                except Exception as e:
                    logger.error(f"投资组合更新任务错误: {e}")
                    
        portfolio_task = asyncio.create_task(portfolio_update_task())
        logger.info("投资组合管理任务已启动!")
        
        # 启动止盈止损监控任务
        async def profit_stop_monitor_task():
            """止盈止损监控任务"""
            while should_continue:
                try:
                    await asyncio.sleep(30)  # 每30秒检查一次止盈止损
                    
                    # 更新持仓状态
                    positions = order_mgr.get_positions()
                    for position in positions:
                        # 获取当前价格
                        quotes = await realtime_mgr.get_quote([position.symbol])
                        if quotes and position.symbol in quotes:
                            current_price = float(quotes[position.symbol].last_done)
                            # 更新持仓状态（使用简化成本价逻辑）
                            # 注意：这里应该从持仓记录中获取真实成本价，暂时使用当前价格作为成本价
                            cost_price = current_price * 0.95  # 假设成本价比当前价格低5%
                            await profit_stop_mgr.update_position_status(
                                position.symbol, 
                                position.quantity, 
                                cost_price, 
                                current_price
                            )
                    
                    # 检查止盈止损信号
                    exit_signals = await profit_stop_mgr.check_exit_signals()
                    
                    # 执行止盈止损信号
                    for signal in exit_signals:
                        success = await profit_stop_mgr.execute_exit_signal(signal)
                        if success:
                            logger.info(f"止盈止损订单执行成功: {signal.symbol} {signal.signal_type}")
                        else:
                            logger.warning(f"止盈止损订单执行失败: {signal.symbol} {signal.signal_type}")
                            
                except Exception as e:
                    logger.error(f"止盈止损监控任务错误: {e}")
                    
        profit_stop_task = asyncio.create_task(profit_stop_monitor_task())
        logger.info("止盈止损监控任务已启动!")
        
        logger.info("系统已启动并运行中，开始主循环...")
        
        # 添加心跳计数器
        heartbeat_counter = 0
        
        # 主循环
        while should_continue:
            try:
                await asyncio.sleep(60)  # 每60秒一次心跳
                heartbeat_counter += 1
                
                # 每5分钟输出一次详细状态
                if heartbeat_counter % 5 == 0:
                    logger.info(f"系统运行正常 - 心跳 #{heartbeat_counter}, 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    
                    # 检查定时任务状态
                    if 'signal_task' in locals():
                        task_status = "运行中" if not signal_task.done() else "已完成/异常"
                        logger.info(f"信号生成任务状态: {task_status}")
                        
                        # 如果任务异常，尝试重新启动
                        if signal_task.done() and signal_task.exception():
                            logger.error(f"信号生成任务异常: {signal_task.exception()}")
                            logger.info("重新启动信号生成任务...")
                            signal_task = asyncio.create_task(signal_gen.scheduled_signal_generation(interval_seconds=signal_interval))
                    
                    if 'portfolio_task' in locals():
                        task_status = "运行中" if not portfolio_task.done() else "已完成/异常"
                        logger.info(f"投资组合管理任务状态: {task_status}")
                        
                        # 如果任务异常，尝试重新启动
                        if portfolio_task.done() and portfolio_task.exception():
                            logger.error(f"投资组合管理任务异常: {portfolio_task.exception()}")
                            logger.info("重新启动投资组合管理任务...")
                            portfolio_task = asyncio.create_task(portfolio_update_task())
                    
                    if 'profit_stop_task' in locals():
                        task_status = "运行中" if not profit_stop_task.done() else "已完成/异常"
                        logger.info(f"止盈止损监控任务状态: {task_status}")
                        
                        # 如果任务异常，尝试重新启动
                        if profit_stop_task.done() and profit_stop_task.exception():
                            logger.error(f"止盈止损监控任务异常: {profit_stop_task.exception()}")
                            logger.info("重新启动止盈止损监控任务...")
                            profit_stop_task = asyncio.create_task(profit_stop_monitor_task())
                        
                        # 输出止盈止损状态摘要
                        summary = profit_stop_mgr.get_status_summary()
                        if summary:
                            logger.info(f"止盈止损状态: 总持仓{summary.get('total_positions', 0)}, "
                                      f"盈利{summary.get('profitable_positions', 0)}, "
                                      f"亏损{summary.get('losing_positions', 0)}, "
                                      f"未实现盈亏{summary.get('total_unrealized_pnl', 0):.2f}")
                    
                    # 检查数据缓存状态
                    cache_info = {}
                    for symbol in args.symbols:
                        if symbol in signal_gen.data_cache:
                            cache_info[symbol] = len(signal_gen.data_cache[symbol])
                        else:
                            cache_info[symbol] = 0
                    logger.info(f"数据缓存状态: {cache_info}")
                else:
                    # 简单心跳
                    logger.debug(f"系统心跳 #{heartbeat_counter}")
                    
            except Exception as e:
                logger.error(f"主循环异常: {e}")
                import traceback
                logger.error(traceback.format_exc())
    except Exception as e:
        logger.error(f"运行时错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 关闭所有组件
        logger.info("关闭所有组件...")
        try:
            await realtime_mgr.stop()
            logger.info("实时数据管理器已关闭")
        except Exception as e:
            logger.error(f"关闭行情管理器错误: {e}")
            
        try:
            await order_mgr.close()
            logger.info("订单管理器已关闭")
        except Exception as e:
            logger.error(f"关闭订单管理器错误: {e}")
            
        # 取消信号生成任务
        if 'signal_task' in locals() and not signal_task.done():
            signal_task.cancel()
            try:
                await signal_task
            except asyncio.CancelledError:
                pass
                
        # 取消投资组合管理任务
        if 'portfolio_task' in locals() and not portfolio_task.done():
            portfolio_task.cancel()
            try:
                await portfolio_task
            except asyncio.CancelledError:
                pass
                
        # 取消止盈止损监控任务
        if 'profit_stop_task' in locals() and not profit_stop_task.done():
            profit_stop_task.cancel()
            try:
                await profit_stop_task
            except asyncio.CancelledError:
                pass
            
        logger.info("系统已安全关闭")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close() 