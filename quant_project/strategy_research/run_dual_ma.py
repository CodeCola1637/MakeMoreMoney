"""
运行双均线策略的入口脚本

用法:
    python run_dual_ma.py --symbol 700.HK --fast 5 --slow 20
"""
import os
import sys
import logging
import argparse
import time
from datetime import datetime

# 添加项目根目录到系统路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# 导入策略
from quant_project.strategy_research.strategies.dual_ma import DualMAStrategy

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("run_dual_ma")

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="运行双均线交叉策略")
    
    # 基本参数
    parser.add_argument("--symbol", type=str, default="700.HK", help="交易品种代码")
    parser.add_argument("--fast", type=int, default=5, help="快速均线周期")
    parser.add_argument("--slow", type=int, default=20, help="慢速均线周期")
    parser.add_argument("--volume", type=int, default=100, help="交易数量")
    parser.add_argument("--api", type=str, default="http://localhost:8002/api", help="交易服务器API地址")
    parser.add_argument("--ma-type", type=str, choices=["simple", "exponential"], 
                        default="simple", help="均线类型: simple或exponential")
    parser.add_argument("--run-time", type=int, default=3600, 
                        help="运行时间(秒)，默认3600秒后自动退出")
    parser.add_argument("--test-order", action="store_true", help="是否执行测试订单")
    
    return parser.parse_args()

def main():
    """主函数"""
    args = parse_args()
    
    # 打印配置信息
    logger.info(f"启动双均线策略，交易品种: {args.symbol}")
    logger.info(f"策略参数: 快线周期={args.fast}, 慢线周期={args.slow}, 均线类型={args.ma_type}")
    logger.info(f"交易数量: {args.volume}, API地址: {args.api}")
    
    # 创建策略实例
    strategy = DualMAStrategy(
        strategy_id=f"dual_ma_{args.fast}_{args.slow}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        symbols=[args.symbol],
        api_url=args.api
    )
    
    # 更新策略参数
    strategy.update_parameters({
        "fast_window": args.fast,
        "slow_window": args.slow,
        "order_volume": args.volume,
        "ma_type": args.ma_type
    })
    
    # 启动策略
    try:
        strategy.start()
        
        # 如果指定了测试订单，先执行一个测试买单
        if args.test_order:
            logger.info("执行测试买单...")
            market_data = strategy.get_market_data()
            current_price = market_data.get(args.symbol, {}).get("price", 0)
            
            if current_price > 0:
                buy_result = strategy.buy(
                    symbol=args.symbol,
                    quantity=args.volume,
                    price=current_price,
                    order_type="limit"
                )
                logger.info(f"测试买单结果: {buy_result}")
                
                # 等待订单处理
                logger.info("等待5秒钟，让订单有时间处理...")
                time.sleep(5)
                
                # 查询持仓
                position = strategy.get_position(args.symbol)
                logger.info(f"当前持仓: {position} 股")
        
        # 运行指定时间
        start_time = time.time()
        end_time = start_time + args.run_time
        
        while time.time() < end_time:
            # 模拟获取最新市场数据并更新策略
            market_data = strategy.get_market_data()
            
            if market_data:
                logger.info(f"获取到市场数据: {args.symbol}")
                strategy.process_market_data(market_data)
                
                # 打印策略状态
                stats = strategy.generate_stats(args.symbol)
                logger.info(
                    f"策略状态: 快线={stats['fast_ma']:.2f}, 慢线={stats['slow_ma']:.2f}, "
                    f"价格={stats['current_price']:.2f}, 持仓={stats['position']}"
                )
            
            # 每60秒检查一次
            time.sleep(60)
            
        logger.info(f"策略运行时间已达 {args.run_time} 秒，准备退出")
        
    except KeyboardInterrupt:
        logger.info("接收到中断信号，准备退出")
    finally:
        # 停止策略
        strategy.stop()
        logger.info("策略已停止")

if __name__ == "__main__":
    main() 