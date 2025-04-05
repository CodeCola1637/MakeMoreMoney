#!/usr/bin/env python3
"""
长桥API测试脚本 - 使用官方SDK
"""

import os
import sys
import asyncio
import logging
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('longport_sdk_test')

# 加载环境变量
load_dotenv()

# API凭证
APP_KEY = os.getenv('LONG_PORT_APP_KEY')
APP_SECRET = os.getenv('LONG_PORT_APP_SECRET')
ACCESS_TOKEN = os.getenv('LONG_PORT_ACCESS_TOKEN')

# 检查凭证
if not APP_KEY or not APP_SECRET or not ACCESS_TOKEN:
    logger.error("缺少API凭证。请在.env文件中设置LONG_PORT_APP_KEY，LONG_PORT_APP_SECRET和LONG_PORT_ACCESS_TOKEN")
    sys.exit(1)

# 导入长桥SDK
try:
    from longport.openapi import Config, QuoteContext, TradeContext, Period, Market, SubType, OrderSide, OrderType, TimeInForceType, AdjustType
    logger.info("成功导入长桥SDK")
except ImportError as e:
    logger.error(f"导入长桥SDK失败: {e}")
    sys.exit(1)

async def test_quote_api():
    """测试行情API"""
    quote_ctx = None
    try:
        logger.info("创建行情API配置...")
        config = Config(APP_KEY, APP_SECRET, ACCESS_TOKEN)
        logger.info("配置创建成功")

        logger.info("连接到行情服务...")
        quote_ctx = QuoteContext(config)
        logger.info("行情服务连接成功")

        # 获取交易时段
        logger.info("获取交易时段...")
        try:
            resp = quote_ctx.trading_session()
            logger.info(f"交易时段: {resp}")
        except Exception as e:
            logger.error(f"获取交易时段失败: {e}")

        # 获取标的基本信息
        symbols = ["700.HK", "AAPL.US"]
        logger.info(f"获取标的 {symbols} 基本信息...")
        try:
            resp = quote_ctx.static_info(symbols=symbols)
            logger.info(f"标的基本信息: {resp}")
        except Exception as e:
            logger.error(f"获取标的基本信息失败: {e}")

        # 获取实时报价
        logger.info(f"获取标的 {symbols} 实时报价...")
        try:
            resp = quote_ctx.quote(symbols=symbols)
            logger.info(f"实时报价: {resp}")
        except Exception as e:
            logger.error(f"获取实时报价失败: {e}")

        # 获取K线数据
        symbol = "700.HK"
        logger.info(f"获取 {symbol} 的K线数据...")
        try:
            count = 10  # 获取最近10根K线
            resp = quote_ctx.candlesticks(symbol=symbol, period=Period.Day, count=count, adjust_type=AdjustType.NoAdjust)
            logger.info(f"K线数据: {resp}")
        except Exception as e:
            logger.error(f"获取K线数据失败: {e}")

        return True
    except Exception as e:
        logger.error(f"行情API测试失败: {e}")
        return False

async def test_trade_api():
    """测试交易API"""
    trade_ctx = None
    try:
        logger.info("创建交易API配置...")
        config = Config(APP_KEY, APP_SECRET, ACCESS_TOKEN)
        logger.info("配置创建成功")

        logger.info("连接到交易服务...")
        trade_ctx = TradeContext(config)
        logger.info("交易服务连接成功")

        # 获取账户余额
        logger.info("获取账户余额...")
        try:
            resp = trade_ctx.account_balance()
            logger.info(f"账户余额: {resp}")
        except Exception as e:
            logger.error(f"获取账户余额失败: {e}")

        # 获取今日订单
        logger.info("获取今日订单...")
        try:
            resp = trade_ctx.today_orders()
            logger.info(f"今日订单: {resp}")
        except Exception as e:
            logger.error(f"获取今日订单失败: {e}")

        # 获取历史订单
        logger.info("获取历史订单...")
        try:
            yesterday = datetime.now() - timedelta(days=1)
            today = datetime.now()
            resp = trade_ctx.history_orders(start_at=yesterday, end_at=today)
            logger.info(f"历史订单: {resp}")
        except Exception as e:
            logger.error(f"获取历史订单失败: {e}")

        return True
    except Exception as e:
        logger.error(f"交易API测试失败: {e}")
        return False

async def main():
    logger.info("===== 开始测试长桥SDK =====")
    
    # 测试行情API
    logger.info("\n===== 测试行情API =====")
    quote_success = await test_quote_api()
    
    # 测试交易API
    logger.info("\n===== 测试交易API =====")
    trade_success = await test_trade_api()
    
    # 报告结果
    logger.info("\n===== 测试结果 =====")
    logger.info(f"行情API测试: {'成功' if quote_success else '失败'}")
    logger.info(f"交易API测试: {'成功' if trade_success else '失败'}")
    
    logger.info("===== 测试完成 =====")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"执行测试时发生错误: {e}")
        sys.exit(1) 