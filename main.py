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
from strategy.breakout_strategy import BreakoutStrategy
from strategy.ccass_tracker import CCASTracker
from strategy.ccass_strategy import CCASStrategy
from execution.order_manager import OrderManager, OrderResult
from execution.task_manager import TaskManager
from tasks import (
    TradingContext,
    ensemble_signal_generation,
    single_strategy_signal_generation,
    volume_anomaly_task,
    portfolio_update,
    profit_stop_monitor,
    health_check,
    stock_discovery_task,
    institutional_tracking_task,
    ccass_tracking_task,
)

# 全局变量
logger = None
task_manager: TaskManager = None

# 创建信号处理回调函数
def create_signal_handler(order_mgr, realtime_mgr=None, portfolio_mgr=None):
    """创建交易信号处理函数
    
    Args:
        order_mgr: 订单管理器
        realtime_mgr: 实时数据管理器，用于订单预验证获取当前价格
        portfolio_mgr: 投资组合管理器，用于关联性检查
    """
    async def on_signal(signal_obj: Signal):
        """处理交易信号"""
        try:
            symbol = signal_obj.symbol
            signal_type = signal_obj.signal_type
            signal_type_val = signal_type.value if hasattr(signal_type, 'value') else str(signal_type)
            price = signal_obj.price
            quantity = signal_obj.quantity
            
            logger.info(f"收到交易信号: {symbol} {signal_type_val} {quantity}股 @ {price}")
            
            if signal_type == SignalType.BUY and portfolio_mgr is not None:
                allowed, reason = portfolio_mgr.check_correlation(symbol)
                if not allowed:
                    logger.info(f"关联性过滤拦截: {symbol} - {reason}")
                    return
            
            await order_mgr.process_signal(signal_obj, realtime_mgr=realtime_mgr)
        except Exception as e:
            logger.error(f"处理信号时出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    return on_signal

# 创建订单更新回调
def create_order_update_handler(profit_stop_mgr=None):
    """创建订单状态更新处理函数"""
    def on_order_update(order_result: OrderResult):
        """处理订单状态更新"""
        logger.info(f"订单状态更新: {order_result}")
        
        if profit_stop_mgr and hasattr(order_result, 'order_id') and hasattr(order_result, 'symbol'):
            status = getattr(order_result, 'status', None)
            status_name = status.value if hasattr(status, 'value') else str(status)
            terminal = {'Filled', 'Rejected', 'Canceled', 'Expired', 'PartiallyFilledCanceled'}
            if status_name in terminal:
                is_filled = (status_name == 'Filled')
                profit_stop_mgr.on_order_completed(
                    str(order_result.order_id), order_result.symbol, is_filled
                )
    
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
    
    # 暴露 historical_loader 给 order_manager（供 ensemble 计算波动率/ATR）
    try:
        order_mgr.historical_loader = hist_loader
    except Exception:
        pass
    
    # 初始化止盈止损管理器（注入 historical_loader 用于 ATR 动态止损）
    profit_stop_mgr = ProfitStopManager(config, order_mgr, historical_loader=hist_loader)
    logger.info("止盈止损管理器初始化完成")
    
    # 🚀 初始化策略组合器
    ensemble_enabled = config.get("ensemble.enable", False)
    sec_strategy = None
    volume_strategy = None
    ccass_strategy = None
    
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
        
        breakout_strategy = BreakoutStrategy(config, hist_loader, logger)
        logger.info("通道突破策略初始化完成")
        
        ccass_strategy = CCASStrategy(config, logger)
        logger.info("CCASS 港股持仓策略适配器初始化完成")
        
        # 创建策略字典（6 策略投票）
        strategies = {
            'lstm': lstm_signal_gen,
            'technical': technical_strategy,
            'sec': sec_strategy,
            'volume_anomaly': volume_strategy,
            'breakout': breakout_strategy,
            'ccass': ccass_strategy,
        }
        
        # 获取组合方法
        ensemble_method_str = config.get("ensemble.method", "confidence_weight")
        try:
            ensemble_method = EnsembleMethod(ensemble_method_str)
        except ValueError:
            logger.warning(f"未知的组合方法: {ensemble_method_str}, 使用默认方法")
            ensemble_method = EnsembleMethod.CONFIDENCE_WEIGHT
        
        # 初始化策略组合器
        signal_gen = StrategyEnsemble(config, strategies, ensemble_method, order_manager=order_mgr, profit_stop_mgr=profit_stop_mgr)
        logger.info(f"策略组合器初始化完成 - 方法: {ensemble_method.value}, 策略数: {len(strategies)}")
        
        # 为LSTM策略单独启动
        await lstm_signal_gen.start(symbols=args.symbols)
        logger.info("LSTM策略启动完成")
        
    else:
        # 传统单一策略模式
        signal_gen = SignalGenerator(config, realtime_mgr, model_trainer, portfolio_mgr)
        logger.info("信号生成器初始化完成（单一策略模式）")
    
    # 创建回调处理函数（传递realtime_mgr用于订单预验证）
    on_signal = create_signal_handler(order_mgr, realtime_mgr, portfolio_mgr)
    on_order_update = create_order_update_handler(profit_stop_mgr)
    
    # 如果指定了训练模式，则先训练模型
    if args.train:
        logger.info("开始训练模型（全量标的联合训练）...")
        try:
            await model_trainer.train_model(symbols=args.symbols, force_retrain=True)
            logger.info(f"模型训练完成，标的: {args.symbols}")
        except Exception as e:
            logger.error(f"模型训练失败: {e}")
    
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
        
        # 📈 初始化 CCASS 持仓追踪模块
        ccass_enabled = config.get("ccass.enable", False)
        ccass_tracker = None
        
        if ccass_enabled:
            try:
                ccass_tracker = CCASTracker(config, logger)
                logger.info("📈 CCASS 持仓追踪模块初始化完成")
            except Exception as e:
                logger.warning(f"CCASS 持仓追踪模块初始化失败: {e}")
                ccass_enabled = False
        
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
        
        # ========== 构建 TradingContext ==========
        ctx = TradingContext(
            config=config,
            symbols=args.symbols,
            realtime_mgr=realtime_mgr,
            order_mgr=order_mgr,
            portfolio_mgr=portfolio_mgr,
            profit_stop_mgr=profit_stop_mgr,
            signal_gen=signal_gen,
            on_signal=on_signal,
            task_manager=task_manager,
            stock_discovery=stock_discovery if discovery_enabled else None,
            institutional_tracker=institutional_tracker if institutional_enabled else None,
            sec_strategy=sec_strategy if ensemble_enabled else None,
            volume_detector=volume_detector if volume_anomaly_enabled else None,
            volume_strategy=volume_strategy if ensemble_enabled else None,
            ccass_tracker=ccass_tracker if ccass_enabled else None,
            ccass_strategy=ccass_strategy if ensemble_enabled else None,
            ensemble_enabled=ensemble_enabled,
        )

        # ========== 使用 TaskManager 创建任务 ==========

        if ensemble_enabled:
            task_manager.create_periodic_task(
                name="signal_generation",
                coro_func=lambda: ensemble_signal_generation(ctx),
                interval=signal_interval,
                max_restarts=10,
                is_critical=True
            )
            logger.info("📡 策略组合器信号生成任务已创建（周期性）")
        else:
            task_manager.create_periodic_task(
                name="signal_generation",
                coro_func=lambda: single_strategy_signal_generation(ctx),
                interval=signal_interval,
                max_restarts=10,
                is_critical=True
            )
            logger.info("📡 单一策略信号生成任务已创建（周期性）")
        
        task_manager.create_periodic_task(
            name="portfolio_update",
            coro_func=lambda: portfolio_update(ctx),
            interval=300,
            max_restarts=5,
            is_critical=False
        )
        logger.info("📈 投资组合管理任务已创建（周期性，间隔300秒）")
        
        task_manager.create_periodic_task(
            name="profit_stop_monitor",
            coro_func=lambda: profit_stop_monitor(ctx),
            interval=30,
            max_restarts=10,
            is_critical=True
        )
        logger.info("🛡️ 止盈止损监控任务已创建（周期性，间隔30秒）")
        
        task_manager.create_periodic_task(
            name="health_check",
            coro_func=lambda: health_check(ctx),
            interval=300,
            max_restarts=3,
            is_critical=False
        )
        logger.info("💓 系统健康检查任务已创建（周期性，间隔300秒）")
        
        if discovery_enabled and stock_discovery:
            discovery_interval = config.get("discovery.scan_interval", 3600)
            task_manager.create_periodic_task(
                name="stock_discovery",
                coro_func=lambda: stock_discovery_task(ctx),
                interval=discovery_interval,
                max_restarts=5,
                is_critical=False
            )
            logger.info(f"🔍 股票发现任务已创建（周期性，间隔{discovery_interval}秒）")
        
        if institutional_enabled and institutional_tracker:
            inst_interval = config.get("institutional.scan_interval", 7200)
            task_manager.create_periodic_task(
                name="institutional_tracking",
                coro_func=lambda: institutional_tracking_task(ctx),
                interval=inst_interval,
                max_restarts=5,
                is_critical=False
            )
            logger.info(f"🏦 机构交易跟踪任务已创建（周期性，间隔{inst_interval}秒）")
        
        if ccass_enabled and ccass_tracker:
            ccass_interval = config.get("ccass.scan_interval", 7200)
            task_manager.create_periodic_task(
                name="ccass_tracking",
                coro_func=lambda: ccass_tracking_task(ctx),
                interval=ccass_interval,
                max_restarts=5,
                is_critical=False
            )
            logger.info(f"📈 CCASS 持仓追踪任务已创建（周期性，间隔{ccass_interval}秒）")
        
        if volume_anomaly_enabled and volume_detector:
            vol_interval = config.get("volume_anomaly.check_interval", 60)
            task_manager.create_periodic_task(
                name="volume_anomaly",
                coro_func=lambda: volume_anomaly_task(ctx),
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