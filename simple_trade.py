"""超级简单的交易脚本"""
import os
import json
import glob
from datetime import datetime

def main():
    # 打印基本信息
    print("开始执行简单交易脚本...")
    print("时间:", datetime.now())
    
    # 获取预测目录
    pred_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/predictions/700_HK'))
    print("预测目录:", pred_dir)
    
    # 检查目录是否存在
    if not os.path.exists(pred_dir):
        print("错误: 预测目录不存在")
        return
        
    # 列出所有预测文件
    pred_files = glob.glob(os.path.join(pred_dir, "700_HK_pred_*.json"))
    if not pred_files:
        print("错误: 没有找到预测文件")
        return
    
    # 获取最新预测
    latest_pred_file = sorted(pred_files)[-1]
    print("最新预测文件:", latest_pred_file)
    
    # 读取预测内容
    try:
        with open(latest_pred_file, 'r') as f:
            prediction = json.load(f)
        print("预测内容:", json.dumps(prediction, indent=2))
    except Exception as e:
        print("读取预测文件时出错:", e)
        return
    
    # 分析预测
    change_pct = prediction.get('predicted_change_pct', 0)
    current_price = prediction.get('current_price', 0)
    predicted_price = prediction.get('predicted_price', 0)
    
    print(f"当前价格: {current_price}")
    print(f"预测价格: {predicted_price}")
    print(f"预测变动: {change_pct}%")
    
    # 决策
    if change_pct > 0.1:
        action = "买入"
    elif change_pct < -0.1:
        action = "卖出"
    else:
        action = "持有"
        
    print(f"交易决策: {action}")
    
    # 保存交易记录
    trade_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/trade_records'))
    os.makedirs(trade_dir, exist_ok=True)
    
    trade_record = {
        "symbol": "700.HK",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "action": action,
        "current_price": current_price,
        "predicted_price": predicted_price,
        "change_pct": change_pct
    }
    
    record_file = os.path.join(trade_dir, f"simple_trade_700_HK_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(record_file, 'w') as f:
        json.dump(trade_record, f, indent=2)
    
    print(f"交易记录已保存至: {record_file}")

if __name__ == "__main__":
    main() 