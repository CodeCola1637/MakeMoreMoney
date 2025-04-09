#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import os
from dotenv import load_dotenv
from utils import setup_longport_env
from execution.order_manager import OrderManager
from utils import ConfigLoader, setup_logger

async def check_account():
    # 设置日志
    logger = setup_logger("check_account", "INFO")
    
    # 加载环境变量和配置
    load_dotenv()
    setup_longport_env()
    config = ConfigLoader()
    
    # 初始化订单管理器
    order_mgr = OrderManager(config)
    try:
        await order_mgr.initialize()
        
        # 获取账户资金
        print('\n===== 可用资金 =====')
        try:
            balance = order_mgr.get_account_balance()  # 不使用await
            if isinstance(balance, list):
                for b in balance:
                    print(f'币种: {b.currency}, 可用: {b.cash}, 总资产: {b.equity}')
            else:
                # 直接访问订单管理器的账户余额字段
                if hasattr(order_mgr, 'account_balance'):
                    balance_dict = order_mgr.account_balance
                    for currency, amount in balance_dict.items():
                        print(f'币种: {currency}, 可用: {amount}')
                else:
                    print(f'可用资金总额: {balance}')
        except Exception as e:
            logger.error(f"获取账户余额失败: {e}")
            print(f"获取账户余额失败: {e}")
        
        # 获取持仓
        print('\n===== 当前持仓 =====')
        try:
            positions = order_mgr.get_positions()  # 不使用await
            if positions and len(positions) > 0:
                for p in positions:
                    print(f'股票: {p.symbol}, 持仓数量: {p.quantity}, 成本价: {p.avg_price}, 市值: {p.market_value}')
            else:
                print('当前无持仓')
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            print(f"获取持仓失败: {e}")
    
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        print(f"初始化失败: {e}")
    finally:
        # 尝试使用正确的方法关闭连接
        try:
            if hasattr(order_mgr, 'close'):
                await order_mgr.close()
            elif hasattr(order_mgr, 'trade_ctx') and hasattr(order_mgr.trade_ctx, 'close'):
                order_mgr.trade_ctx.close()
            else:
                logger.info("无需关闭连接")
        except Exception as e:
            logger.error(f"关闭连接失败: {e}")
            print(f"关闭连接失败: {e}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(check_account()) 