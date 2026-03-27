#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
智能量化交易系统 - 主入口
重构版本：使用 TaskManager 统一管理异步任务
"""

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

from utils import ConfigLoader, setup_logger, setup_logging, setup_longport_env
from databases.db import init_db
from data_loader.realtime import RealtimeDataManager
from data_loader.historical import HistoricalDataLoader
from strategy.train import LSTMModelTrainer
from strategy.signals import SignalGenerator, Signal, SignalType
from strategy.technical_strategy import TechnicalStrategy
from strategy.strategy_ensemble import StrategyEnsemble, EnsembleMethod
from strategy.portfolio_manager import PortfolioManager
from strategy.profit_stop_manager import ProfitStopManager
from strategy.stock_discovery import StockDiscovery
from strategy.institutional_tracker import InstitutionalTracker
from strategy.volume_anomaly_detector import VolumeAnomalyDetector
from strategy.sec_strategy import SECStrategy
from strategy.volume_strategy import VolumeStrategy
from execution.order_manager import OrderManager, OrderResult
from execution.task_manager import TaskManager

# 全局变量
logger = None
task_manager: TaskManager = None

# 创建信号处理回调函数
def create_signal_handler(order_mgr, realtime_mgr=None):
    """创建交易信号处理函数
    
    Args:
        order_mgr: 订单管理器
        realtime_mgr: 实时数据管理器，用于订单预验证获取当前价格
    """
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
            
            # 触发订单执行（传递realtime_mgr用于订单预验证）
            await order_mgr.process_signal(signal_obj, realtime_mgr=realtime_mgr)
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
    global task_manager
    if logger:
        logger.info("接收到关闭信号，开始优雅关闭...")
    if task_manager:
        # 触发任务管理器关闭
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda: asyncio.create_task(task_manager.shutdown())
        )

# 信号处理
def setup_signal_handlers():
    """设置信号处理器"""
    signal.signal(signal.SIGINT, lambda s, f: shutdown())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown())

async def main():
    global logger, task_manager
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="量化交易系统")
    parser.add_argument("--train", action="store_true", help="训练模型")
    parser.add_argument("--symbols", nargs="+", default=None, help="要交易的股票代码（如果不指定，使用配置文件中的股票）")
    parser.add_argument("--no-mock", action="store_true", help="禁止使用模拟数据，使用实时行情")
    parser.add_argument("--dashboard", action="store_true", help="同时启动 Web 仪表盘")
    parser.add_argument("--dashboard-port", type=int, default=8888, help="仪表盘端口（默认 8888）")
    args = parser.parse_args()
    
    # 加载环境变量
    load_dotenv()
    
    # 加载配置（单例模式）
    config = ConfigLoader.get_instance()
    
    # 设置统一日志系统
    setup_logging(config)
    logger = setup_logger("main", config.get("logging.level", "INFO"))
    logger.info("🚀 智能量化交易系统启动中...")
    logger.info("配置加载完成（单例模式）")
    logger.info("统一日志系统已初始化")
    
    # 设置长桥API环境变量
    setup_longport_env(config)
    
    # 设置信号处理
    setup_signal_handlers()
    
    # 初始化任务管理器
    task_manager = TaskManager(logger)
    await task_manager.start()
    logger.info("✅ TaskManager 初始化完成")
    
    # 启动 Web 仪表盘（在后台线程中运行）
    if args.dashboard:
        import threading
        from web.dashboard import start_dashboard
        dash_port = args.dashboard_port
        dash_thread = threading.Thread(
            target=start_dashboard,
            kwargs={"host": "0.0.0.0", "port": dash_port},
            daemon=True,
        )
        dash_thread.start()
        logger.info(f"📊 Web 仪表盘已启动: http://localhost:{dash_port}")
    
    # 🔧 优先使用配置文件中的股票列表，如果命令行没有指定的话
    if args.symbols is None:
        args.symbols = config.get("quote.symbols", ["AAPL.US"])
        logger.info(f"使用配置文件中的股票列表: {args.symbols}")
    else:
        logger.info(f"使用命令行指定的股票列表: {args.symbols}")
    
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
    
    # 🚀 初始化策略组合器
    ensemble_enabled = config.get("ensemble.enable", False)
    
    if ensemble_enabled:
        logger.info("初始化策略组合器...")
        
        # 初始化各个策略
        lstm_signal_gen = SignalGenerator(config, realtime_mgr, model_trainer, portfolio_mgr)
        logger.info("LSTM信号生成器初始化完成")
        
        technical_strategy = TechnicalStrategy(config, realtime_mgr, hist_loader)
        logger.info("技术指标策略初始化完成")
        
        # 创建策略适配器
        sec_strategy = SECStrategy(config, logger)
        logger.info("SEC 披露策略适配器初始化完成")
        
        volume_strategy = VolumeStrategy(config, logger)
        logger.info("异常成交量策略适配器初始化完成")
        
        # 创建策略字典（4 策略投票）
        strategies = {
            'lstm': lstm_signal_gen,
            'technical': technical_strategy,
            'sec': sec_strategy,
            'volume_anomaly': volume_strategy,
        }
        
        # 获取组合方法
        ensemble_method_str = config.get("ensemble.method", "confidence_weight")
        try:
            ensemble_method = EnsembleMethod(ensemble_method_str)
        except ValueError:
            logger.warning(f"未知的组合方法: {ensemble_method_str}, 使用默认方法")
            ensemble_method = EnsembleMethod.CONFIDENCE_WEIGHT
        
        # 初始化策略组合器
        signal_gen = StrategyEnsemble(config, strategies, ensemble_method)
        logger.info(f"策略组合器初始化完成 - 方法: {ensemble_method.value}, 策略数: {len(strategies)}")
        
        # 为LSTM策略单独启动
        await lstm_signal_gen.start(symbols=args.symbols)
        logger.info("LSTM策略启动完成")
        
    else:
        # 传统单一策略模式
        signal_gen = SignalGenerator(config, realtime_mgr, model_trainer, portfolio_mgr)
        logger.info("信号生成器初始化完成（单一策略模式）")
    
    # 创建回调处理函数（传递realtime_mgr用于订单预验证）
    on_signal = create_signal_handler(order_mgr, realtime_mgr)
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
    if not ensemble_enabled:
        # 单一策略模式需要注册回调和启动
        signal_gen.register_callback(on_signal)
        # 启动信号生成器（这里会预填充历史数据）
        logger.info("启动信号生成器并预填充历史数据...")
        await signal_gen.start(symbols=args.symbols)
        logger.info("信号生成器启动完成")
    else:
        # 策略组合器模式，回调由策略组合器处理
        logger.info("策略组合器模式，跳过单独的信号生成器启动")
        
    order_mgr.register_order_callback(on_order_update)
    
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
        
        # 🔍 初始化股票发现模块
        discovery_enabled = config.get("discovery.enable", False)
        stock_discovery = None
        
        if discovery_enabled:
            try:
                from longport.openapi import QuoteContext, Config as LPConfig
                import yaml
                
                # 创建独立的行情上下文用于股票发现
                with open('config.yaml', 'r') as f:
                    api_cfg = yaml.safe_load(f)
                
                lp_config = LPConfig(
                    app_key=api_cfg['api']['app_key'],
                    app_secret=api_cfg['api']['app_secret'],
                    access_token=api_cfg['api']['access_token']
                )
                discovery_quote_ctx = QuoteContext(lp_config)
                
                stock_discovery = StockDiscovery(discovery_quote_ctx, config, logger)
                logger.info("🔍 股票发现模块初始化完成")
            except Exception as e:
                logger.warning(f"股票发现模块初始化失败: {e}")
                discovery_enabled = False
        
        # 🏦 初始化机构交易跟踪模块
        institutional_enabled = config.get("institutional.enable", False)
        institutional_tracker = None
        
        if institutional_enabled:
            try:
                institutional_tracker = InstitutionalTracker(config, logger)
                logger.info("🏦 机构交易跟踪模块初始化完成")
            except Exception as e:
                logger.warning(f"机构交易跟踪模块初始化失败: {e}")
                institutional_enabled = False
        
        # 📊 初始化异常成交量检测模块
        volume_anomaly_enabled = config.get("volume_anomaly.enable", False)
        volume_detector = None
        
        if volume_anomaly_enabled:
            try:
                volume_detector = VolumeAnomalyDetector(config, realtime_mgr, hist_loader, logger)
                await volume_detector.start(args.symbols)
                logger.info("📊 异常成交量检测模块初始化完成")
            except Exception as e:
                logger.warning(f"异常成交量检测模块初始化失败: {e}")
                volume_anomaly_enabled = False
        
        # 🚀 使用 TaskManager 创建和管理所有任务
        logger.info("准备使用 TaskManager 启动所有后台任务...")
        signal_interval = config.get("strategy.signal_interval", 30)
        logger.info(f"信号生成间隔设置为 {signal_interval} 秒")
        
        # ========== 定义任务函数 ==========
        
        # 1. 信号生成任务（组合器模式）
        async def ensemble_signal_generation():
            """策略组合器信号生成任务（单次执行）"""
            start_time = datetime.now()
            signals_generated = 0
            
            for symbol in args.symbols:
                try:
                    quotes = await realtime_mgr.get_quote([symbol])
                    if quotes and symbol in quotes:
                        market_data = {
                            'last_done': float(quotes[symbol].last_done),
                            'timestamp': datetime.now().isoformat()
                        }
                        
                        ensemble_signal = await signal_gen.generate_ensemble_signal(symbol, market_data)
                        
                        if ensemble_signal and ensemble_signal.signal_type.value != 'HOLD':
                            logger.info(f"📊 组合信号: {symbol} - {ensemble_signal.signal_type.value}, "
                                      f"置信度: {ensemble_signal.confidence:.3f}")
                            await on_signal(ensemble_signal)
                            signals_generated += 1
                    else:
                        logger.warning(f"无法获取 {symbol} 的最新行情数据")
                        
                except Exception as e:
                    logger.error(f"处理组合信号失败: {symbol}, 错误: {e}")
                    
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"✅ 信号生成完成，耗时: {duration:.2f}秒, 生成信号: {signals_generated}个")
        
        # 2. 单一策略信号生成任务
        async def single_strategy_signal_generation():
            """单一策略信号生成任务（单次执行）"""
            await signal_gen.generate_all_signals()
        
        # 3. 投资组合更新任务
        async def portfolio_update():
            """投资组合更新任务（单次执行）"""
            try:
                await portfolio_mgr.update_portfolio_status()
                
                if portfolio_mgr.portfolio_status and portfolio_mgr.portfolio_status.rebalance_needed:
                    logger.info("检测到需要再平衡，开始执行...")
                    await portfolio_mgr.execute_rebalance()
            except Exception as e:
                logger.error(f"投资组合更新错误: {e}")
        
        # 4. 止盈止损监控任务
        async def profit_stop_monitor():
            """止盈止损监控任务（单次执行）"""
            try:
                positions = order_mgr.get_positions()
                for position in positions:
                    quotes = await realtime_mgr.get_quote([position.symbol])
                    if quotes and position.symbol in quotes:
                        current_price = float(quotes[position.symbol].last_done)
                        
                        # 获取真实成本价
                        cost_price = getattr(position, 'cost_price', None)
                        if cost_price is not None:
                            cost_price = float(cost_price)
                        else:
                            cost_price = profit_stop_mgr.get_real_cost_price(position.symbol)
                            if cost_price is None:
                                cost_price = current_price
                                logger.warning(f"⚠️ 无法获取 {position.symbol} 真实成本价")
                        
                        await profit_stop_mgr.update_position_status(
                            position.symbol, 
                            int(position.quantity), 
                            cost_price, 
                            current_price
                        )
                
                exit_signals = await profit_stop_mgr.check_exit_signals()
                for sig in exit_signals:
                    success = await profit_stop_mgr.execute_exit_signal(sig)
                    if success:
                        logger.info(f"止盈止损订单执行成功: {sig.symbol}")
                    else:
                        logger.warning(f"止盈止损订单执行失败: {sig.symbol}")
                        
            except Exception as e:
                logger.error(f"止盈止损监控错误: {e}")
        
        # 5. 系统健康检查任务
        async def health_check():
            """系统健康检查任务（单次执行）"""
            health_report = task_manager.get_health_report()
            
            if not health_report['is_healthy']:
                logger.warning(f"⚠️ 系统健康状态异常: 失败任务={health_report['failed_tasks']}")
            else:
                logger.info(f"✅ 系统运行正常: 任务={health_report['total_tasks']}, "
                          f"运行中={health_report['running_tasks']}")
            
            # 输出止盈止损摘要
            summary = profit_stop_mgr.get_status_summary()
            if summary:
                logger.info(f"📊 持仓状态: 总{summary.get('total_positions', 0)}, "
                          f"盈利{summary.get('profitable_positions', 0)}, "
                          f"亏损{summary.get('losing_positions', 0)}")
        
        # 6. 股票发现任务
        async def stock_discovery_task():
            """股票发现任务（单次执行）"""
            if not stock_discovery:
                return
            
            try:
                # 运行发现周期
                ready_stocks = await stock_discovery.run_discovery_cycle()
                
                # 输出观察列表摘要
                logger.info(stock_discovery.get_watch_list_summary())
                
                # 如果有准备入场的股票，生成信号
                auto_trade = config.get("discovery.auto_trade", False)
                if ready_stocks and auto_trade:
                    for candidate in ready_stocks:
                        try:
                            # 生成买入信号
                            signal = Signal(
                                symbol=candidate.symbol,
                                signal_type=SignalType.BUY,
                                price=candidate.entry_price,
                                quantity=1,  # 最小数量，实际由订单管理器计算
                                confidence=candidate.confidence,
                                strategy_name="discovery"
                            )
                            await on_signal(signal)
                            logger.info(f"🎯 发现模块生成买入信号: {candidate.symbol}")
                        except Exception as e:
                            logger.error(f"生成发现信号失败: {candidate.symbol}, {e}")
                elif ready_stocks:
                    # 仅提醒，不自动交易
                    for candidate in ready_stocks:
                        logger.info(f"💡 发现入场机会（未启用自动交易）: {candidate.symbol} "
                                  f"@ {candidate.current_price:.2f}, "
                                  f"原因: {candidate.discovery_reason.value}")
                        
            except Exception as e:
                logger.error(f"股票发现任务错误: {e}")
        
        # 7. 机构交易跟踪任务（更新 SEC 策略缓存，由 ensemble 统一决策）
        async def institutional_tracking_task():
            """机构交易跟踪任务 — 扫描 SEC 数据并更新策略缓存"""
            if not institutional_tracker:
                return
            
            try:
                signals = await institutional_tracker.run_scan_cycle(watch_symbols=args.symbols)
                
                logger.info(institutional_tracker.get_summary())
                
                if signals:
                    sec_strategy.update_signals(signals)
                    for inst_sig in signals:
                        logger.info(f"🏦 SEC 信号已缓存: {inst_sig.symbol} "
                                  f"{inst_sig.signal_type}, 置信度={inst_sig.confidence:.2f}, "
                                  f"{inst_sig.reason}")
                        
            except Exception as e:
                logger.error(f"机构交易跟踪任务错误: {e}")
        
        # 8. 异常成交量检测任务（更新缓存 + 即时触发 ensemble 评估）
        async def volume_anomaly_task():
            """异常成交量检测 — 更新策略缓存并立即触发 ensemble 投票"""
            if not volume_detector:
                return
            
            try:
                vol_signals = await volume_detector.check_and_generate_signals()
                
                if not vol_signals:
                    return
                
                logger.info(volume_detector.get_summary())
                
                # 更新 VolumeStrategy 缓存
                triggered_symbols = volume_strategy.update_signals(vol_signals)
                
                # 立即对受影响的股票触发 ensemble 4 策略投票
                if ensemble_enabled and triggered_symbols:
                    for symbol in triggered_symbols:
                        try:
                            quotes = await realtime_mgr.get_quote([symbol])
                            if not (quotes and symbol in quotes):
                                logger.warning(f"即时评估: 无法获取 {symbol} 行情，跳过")
                                continue
                            
                            market_data = {
                                'last_done': float(quotes[symbol].last_done),
                                'timestamp': datetime.now().isoformat(),
                            }
                            
                            ensemble_signal = await signal_gen.generate_ensemble_signal(
                                symbol, market_data
                            )
                            
                            if ensemble_signal and ensemble_signal.signal_type.value != 'HOLD':
                                logger.info(
                                    f"⚡ 即时 Ensemble 投票结果: {symbol} "
                                    f"{ensemble_signal.signal_type.value}, "
                                    f"置信度={ensemble_signal.confidence:.3f}, "
                                    f"策略={ensemble_signal.extra_data.get('contributing_strategies', [])}"
                                )
                                await on_signal(ensemble_signal)
                            else:
                                logger.info(
                                    f"📊 即时 Ensemble: {symbol} 综合判定 HOLD, "
                                    f"暂不交易"
                                )
                        except Exception as e:
                            logger.error(f"即时 ensemble 评估失败 {symbol}: {e}")
                        
            except Exception as e:
                logger.error(f"异常成交量检测任务错误: {e}")
        
        # ========== 使用 TaskManager 创建任务 ==========
        
        # 信号生成任务
        if ensemble_enabled:
            task_manager.create_periodic_task(
                name="signal_generation",
                coro_func=ensemble_signal_generation,
                interval=signal_interval,
                max_restarts=10,
                is_critical=True
            )
            logger.info("📡 策略组合器信号生成任务已创建（周期性）")
        else:
            task_manager.create_periodic_task(
                name="signal_generation",
                coro_func=single_strategy_signal_generation,
                interval=signal_interval,
                max_restarts=10,
                is_critical=True
            )
            logger.info("📡 单一策略信号生成任务已创建（周期性）")
        
        # 投资组合管理任务（每5分钟）
        task_manager.create_periodic_task(
            name="portfolio_update",
            coro_func=portfolio_update,
            interval=300,
            max_restarts=5,
            is_critical=False
        )
        logger.info("📈 投资组合管理任务已创建（周期性，间隔300秒）")
        
        # 止盈止损监控任务（每30秒）
        task_manager.create_periodic_task(
            name="profit_stop_monitor",
            coro_func=profit_stop_monitor,
            interval=30,
            max_restarts=10,
            is_critical=True
        )
        logger.info("🛡️ 止盈止损监控任务已创建（周期性，间隔30秒）")
        
        # 系统健康检查任务（每5分钟）
        task_manager.create_periodic_task(
            name="health_check",
            coro_func=health_check,
            interval=300,
            max_restarts=3,
            is_critical=False
        )
        logger.info("💓 系统健康检查任务已创建（周期性，间隔300秒）")
        
        # 股票发现任务（每小时）
        if discovery_enabled and stock_discovery:
            discovery_interval = config.get("discovery.scan_interval", 3600)
            task_manager.create_periodic_task(
                name="stock_discovery",
                coro_func=stock_discovery_task,
                interval=discovery_interval,
                max_restarts=5,
                is_critical=False
            )
            logger.info(f"🔍 股票发现任务已创建（周期性，间隔{discovery_interval}秒）")
        
        # 机构交易跟踪任务（每2小时）
        if institutional_enabled and institutional_tracker:
            inst_interval = config.get("institutional.scan_interval", 7200)
            task_manager.create_periodic_task(
                name="institutional_tracking",
                coro_func=institutional_tracking_task,
                interval=inst_interval,
                max_restarts=5,
                is_critical=False
            )
            logger.info(f"🏦 机构交易跟踪任务已创建（周期性，间隔{inst_interval}秒）")
        
        # 异常成交量检测任务
        if volume_anomaly_enabled and volume_detector:
            vol_interval = config.get("volume_anomaly.check_interval", 60)
            task_manager.create_periodic_task(
                name="volume_anomaly",
                coro_func=volume_anomaly_task,
                interval=vol_interval,
                max_restarts=5,
                is_critical=False
            )
            logger.info(f"📊 异常成交量检测任务已创建（周期性，间隔{vol_interval}秒）")
        
        logger.info("="*60)
        logger.info("🎉 所有任务已通过 TaskManager 启动！")
        logger.info("="*60)
        
        logger.info("系统已启动并运行中，进入主循环...")
        
        # 主循环 - 等待 TaskManager 关闭信号
        try:
            while task_manager.is_running:
                await asyncio.sleep(60)  # 每60秒检查一次
                
                # 输出 TaskManager 状态摘要
                if task_manager.is_running:
                    logger.debug(task_manager.get_summary())
                    
        except asyncio.CancelledError:
            logger.info("主循环被取消")
    except Exception as e:
        logger.error(f"运行时错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 使用 TaskManager 优雅关闭所有任务
        logger.info("="*60)
        logger.info("开始优雅关闭系统...")
        
        # 1. 关闭 TaskManager 管理的所有任务
        if task_manager:
            try:
                await task_manager.shutdown(timeout=30)
                logger.info("✅ TaskManager 已关闭所有任务")
            except Exception as e:
                logger.error(f"TaskManager 关闭错误: {e}")
        
        # 2. 关闭实时数据管理器
        try:
            await realtime_mgr.stop()
            logger.info("✅ 实时数据管理器已关闭")
        except Exception as e:
            logger.error(f"关闭实时数据管理器错误: {e}")
            
        # 3. 关闭订单管理器
        try:
            await order_mgr.close()
            logger.info("✅ 订单管理器已关闭")
        except Exception as e:
            logger.error(f"关闭订单管理器错误: {e}")
            
        logger.info("="*60)
        logger.info("🏁 系统已安全关闭")
        logger.info("="*60)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close() 