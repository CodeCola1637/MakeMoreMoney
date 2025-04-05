"""
执行交易的脚本 - 简化版
根据预测结果模拟执行交易
"""
import os
import sys
import json
import logging
import argparse
from datetime import datetime
import glob

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("trade_executor")

# 设置控制台输出
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 全局配置
PRED_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/predictions'))
RECORDS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/trade_records'))

def get_latest_prediction(symbol):
    """获取最新的预测结果"""
    safe_symbol = symbol.replace(".", "_")
    pred_dir = os.path.join(PRED_DIR, safe_symbol)
    
    if not os.path.exists(pred_dir):
        logger.error(f"未找到 {symbol} 的预测目录")
        return None
    
    # 查找所有预测文件
    pred_files = glob.glob(os.path.join(pred_dir, f"{safe_symbol}_pred_*.json"))
    if not pred_files:
        logger.error(f"未找到 {symbol} 的预测文件")
        return None
    
    # 按文件名排序，获取最新的预测
    latest_pred_file = sorted(pred_files)[-1]
    logger.info(f"找到最新预测文件: {latest_pred_file}")
    
    try:
        with open(latest_pred_file, 'r') as f:
            prediction = json.load(f)
        return prediction
    except Exception as e:
        logger.error(f"加载预测文件出错: {e}")
        return None

def simulate_trade(symbol, prediction, amount=10000):
    """模拟交易"""
    # 获取预测的价格变动
    change_pct = prediction.get('predicted_change_pct', 0)
    current_price = prediction.get('current_price', 0)
    predicted_price = prediction.get('predicted_price', 0)
    
    # 确定交易方向
    if change_pct > 0.1:  # 涨幅超过0.1%才买入
        direction = "买入"
        action = "买入"
    elif change_pct < -0.1:  # 跌幅超过0.1%才卖出
        direction = "卖出"
        action = "卖出"
    else:
        direction = "持有"
        action = "不操作"
    
    # 计算交易数量
    quantity = int(amount / current_price)
    
    logger.info(f"交易决策: {symbol}")
    logger.info(f"  当前价格: {current_price:.2f}")
    logger.info(f"  预测价格: {predicted_price:.2f}")
    logger.info(f"  预测变动: {change_pct:.2f}%")
    logger.info(f"  交易方向: {direction}")
    logger.info(f"  建议操作: {action}")
    
    if action == "不操作":
        logger.info("预测变动较小，不进行交易")
        return None
    
    logger.info(f"  交易数量: {quantity} 股")
    logger.info(f"  交易金额: {quantity * current_price:.2f}")
    
    logger.info("模拟交易模式，记录交易信息")
    
    # 返回交易信息
    return {
        "symbol": symbol,
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "direction": direction,
        "quantity": quantity,
        "price": current_price,
        "amount": quantity * current_price,
        "predicted_change_pct": change_pct,
        "predicted_price": predicted_price
    }

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='根据预测结果执行交易')
    parser.add_argument('--symbol', type=str, required=True, help='股票代码')
    parser.add_argument('--amount', type=float, default=10000, help='交易金额')
    return parser.parse_args()

def main():
    """主函数"""
    args = parse_args()
    symbol = args.symbol
    
    # 测试日志输出
    print("开始执行交易脚本...")
    logger.info("日志系统初始化完成")
    
    # 获取预测结果
    prediction = get_latest_prediction(symbol)
    
    if prediction is None:
        logger.error(f"未找到 {symbol} 的有效预测结果")
        return
    
    # 检查预测日期是否为今天
    predict_date = prediction.get('predict_date')
    today = datetime.now().strftime('%Y-%m-%d')
    
    logger.info(f"预测日期: {predict_date}")
    logger.info(f"今天日期: {today}")
    
    # 执行模拟交易
    trade_info = simulate_trade(symbol, prediction, amount=args.amount)
    
    if trade_info:
        # 保存交易记录
        trade_record = {
            'prediction': prediction,
            'trade': trade_info
        }
        
        # 保存交易记录
        os.makedirs(RECORDS_DIR, exist_ok=True)
        
        record_file = os.path.join(RECORDS_DIR, f"trade_{symbol.replace('.', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(record_file, 'w') as f:
            json.dump(trade_record, f, indent=2)
        
        logger.info(f"交易记录已保存至: {record_file}")

if __name__ == "__main__":
    main() 