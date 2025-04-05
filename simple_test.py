#!/usr/bin/env python3
"""
严格按照官方文档示例测试长桥API
"""
import os
import logging
import sys
from dotenv import load_dotenv

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("simple_test")

try:
    # 尝试导入长桥SDK
    from longport.openapi import Config, QuoteContext, TradeContext
    logger.info("成功导入长桥SDK")
except ImportError as e:
    logger.error(f"导入长桥SDK失败: {e}")
    sys.exit(1)

def main():
    """主函数"""
    logger.info("===== 长桥API简单测试 =====")
    
    # 加载环境变量
    load_dotenv()
    
    # 获取API凭证
    app_key = os.getenv("LONG_PORT_APP_KEY")
    app_secret = os.getenv("LONG_PORT_APP_SECRET")
    access_token = os.getenv("LONG_PORT_ACCESS_TOKEN")
    
    if not app_key or not app_secret or not access_token:
        logger.error("API凭证未设置，请检查环境变量")
        return
    
    logger.info(f"使用APP KEY: {app_key[:6]}***")
    
    try:
        # 直接使用文档中的方式来创建配置
        logger.info("创建API配置...")
        config = Config(
            app_key=app_key,
            app_secret=app_secret,
            access_token=access_token
        )
        logger.info("创建API配置成功")
        
        # 尝试创建行情上下文
        try:
            logger.info("创建行情上下文...")
            quote_ctx = QuoteContext(config)
            logger.info("行情上下文创建成功")
            
            # 获取基本行情数据
            try:
                logger.info("获取股票行情...")
                quote_data = quote_ctx.quote(["700.HK", "AAPL.US"])
                logger.info(f"行情数据获取成功: {quote_data}")
            except Exception as e:
                logger.error(f"获取行情数据失败: {e}")
        except Exception as e:
            logger.error(f"创建行情上下文失败: {e}")
        
        # 尝试创建交易上下文
        try:
            logger.info("创建交易上下文...")
            trade_ctx = TradeContext(config)
            logger.info("交易上下文创建成功")
            
            # 获取账户信息
            try:
                logger.info("获取账户余额...")
                balance = trade_ctx.account_balance()
                logger.info(f"账户余额获取成功: {balance}")
            except Exception as e:
                logger.error(f"获取账户余额失败: {e}")
                
            # 获取今日订单
            try:
                logger.info("获取今日订单...")
                orders = trade_ctx.today_orders()
                logger.info(f"获取今日订单成功: {len(orders)} 个订单")
            except Exception as e:
                logger.error(f"获取今日订单失败: {e}")
        except Exception as e:
            logger.error(f"创建交易上下文失败: {e}")
    
    except Exception as e:
        logger.error(f"测试过程中发生错误: {e}")
    
    logger.info("===== 测试完成 =====")

if __name__ == "__main__":
    main() 