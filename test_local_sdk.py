#!/usr/bin/env python
"""
使用本地SDK源码测试长桥API连接
"""
import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv

# 添加本地SDK到Python路径
sys.path.insert(0, os.path.abspath('./openapi/python/pysrc'))

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,  # 使用DEBUG级别以获取更多信息
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("local_sdk_test")

try:
    # 尝试导入本地SDK
    from longport.openapi import Config, QuoteContext, TradeContext
    logger.info("成功导入本地长桥SDK")
except ImportError as e:
    logger.error(f"导入本地SDK失败: {e}")
    sys.exit(1)

def test_config():
    """测试API配置"""
    # 加载环境变量
    load_dotenv()
    
    # 获取API凭证
    app_key = os.getenv("LONG_PORT_APP_KEY")
    app_secret = os.getenv("LONG_PORT_APP_SECRET")
    access_token = os.getenv("LONG_PORT_ACCESS_TOKEN")
    http_url = os.getenv("API_BASE_URL", "https://open-api.longportapp.com")
    
    logger.info(f"使用 APP KEY: {app_key[:6]}***")
    logger.info(f"API URL: {http_url}")
    
    try:
        # 创建配置
        config = Config(
            app_key=app_key,
            app_secret=app_secret,
            access_token=access_token,
            http_url=http_url
        )
        logger.info("成功创建API配置")
        return config
    except Exception as e:
        logger.error(f"创建API配置失败: {e}")
        return None

def test_quote_api(config):
    """测试行情API"""
    if not config:
        logger.error("无法测试行情API: 配置无效")
        return
    
    try:
        # 创建行情上下文
        quote_ctx = QuoteContext(config)
        logger.info("成功创建行情上下文")
        
        # 获取市场状态
        try:
            market_state = quote_ctx.trading_session()
            logger.info(f"获取市场状态成功: {market_state}")
        except Exception as e:
            logger.error(f"获取市场状态失败: {e}")
            
        # 尝试获取股票基本信息
        try:
            symbol = "AAPL.US"
            stock_info = quote_ctx.quote([symbol])
            logger.info(f"获取 {symbol} 基本信息成功: {stock_info}")
        except Exception as e:
            logger.error(f"获取股票基本信息失败: {e}")
            
    except Exception as e:
        logger.error(f"创建行情上下文失败: {e}")

def test_trade_api(config):
    """测试交易API"""
    if not config:
        logger.error("无法测试交易API: 配置无效")
        return
    
    try:
        # 创建交易上下文
        trade_ctx = TradeContext(config)
        logger.info("成功创建交易上下文")
        
        # 获取账户余额
        try:
            account_balance = trade_ctx.account_balance()
            logger.info(f"获取账户余额成功: {account_balance}")
        except Exception as e:
            logger.error(f"获取账户余额失败: {e}")
            
        # 获取今日订单
        try:
            today_orders = trade_ctx.today_orders()
            logger.info(f"获取今日订单成功: {len(today_orders)} 个订单")
        except Exception as e:
            logger.error(f"获取今日订单失败: {e}")
            
    except Exception as e:
        logger.error(f"创建交易上下文失败: {e}")

if __name__ == "__main__":
    logger.info("开始测试本地SDK")
    
    try:
        # 测试配置
        config = test_config()
        
        # 测试行情API
        test_quote_api(config)
        
        # 测试交易API
        test_trade_api(config)
        
    except Exception as e:
        logger.error(f"测试过程中发生错误: {e}")
        
    logger.info("测试完成") 