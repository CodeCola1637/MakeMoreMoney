#!/usr/bin/env python3
"""
交易绩效分析工具
分析历史交易记录，计算盈亏，生成报告
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
import yaml

def load_config():
    """加载配置文件"""
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"⚠️ 加载配置文件失败: {e}")
        return {}

def analyze_orders():
    """分析订单数据"""
    try:
        # 读取订单数据
        df = pd.read_csv('logs/orders.csv')
        print(f"📊 总订单数量: {len(df)}")
        
        # 基本统计
        print("\n=== 基本统计 ===")
        print(f"订单状态分布:")
        status_counts = df['status'].value_counts()
        for status, count in status_counts.items():
            print(f"  {status}: {count} ({count/len(df)*100:.1f}%)")
        
        # 按交易对分析
        print(f"\n=== 按交易对分析 ===")
        symbol_counts = df['symbol'].value_counts()
        for symbol, count in symbol_counts.items():
            success_rate = len(df[(df['symbol'] == symbol) & (df['status'] != 'OrderStatus.Rejected')]) / count * 100
            print(f"  {symbol}: {count} 单 (成功率: {success_rate:.1f}%)")
        
        # 成功/失败订单分析
        successful_orders = df[df['status'] != 'OrderStatus.Rejected']
        rejected_orders = df[df['status'] == 'OrderStatus.Rejected']
        
        print(f"\n=== 成功订单分析 ===")
        print(f"成功订单数: {len(successful_orders)}")
        print(f"拒绝订单数: {len(rejected_orders)}")
        print(f"总成功率: {len(successful_orders)/len(df)*100:.1f}%")
        
        # 拒绝原因分析
        print(f"\n=== 拒绝原因分析 ===")
        if 'rejected_at' in df.columns:
            rejection_reasons = rejected_orders['rejected_at'].value_counts().head(10)
            for reason, count in rejection_reasons.items():
                if pd.notna(reason):
                    print(f"  {reason}: {count} 次")
        
        return df
        
    except Exception as e:
        print(f"❌ 分析订单数据失败: {e}")
        return None

def analyze_by_time(df):
    """按时间分析交易模式"""
    try:
        print(f"\n=== 时间模式分析 ===")
        
        # 转换时间
        df['submitted_at'] = pd.to_datetime(df['submitted_at'])
        df['date'] = df['submitted_at'].dt.date
        df['hour'] = df['submitted_at'].dt.hour
        
        # 按日期分析
        daily_stats = df.groupby('date').agg({
            'order_id': 'count',
            'status': lambda x: (x != 'OrderStatus.Rejected').sum()
        }).rename(columns={'order_id': 'total_orders', 'status': 'successful_orders'})
        
        daily_stats['success_rate'] = daily_stats['successful_orders'] / daily_stats['total_orders'] * 100
        
        print(f"日均订单数: {daily_stats['total_orders'].mean():.1f}")
        print(f"日均成功率: {daily_stats['success_rate'].mean():.1f}%")
        
        # 按小时分析
        hourly_stats = df.groupby('hour').size()
        print(f"\n最活跃交易时间:")
        for hour, count in hourly_stats.nlargest(5).items():
            print(f"  {hour:02d}:00 - {count} 单")
        
        return daily_stats, hourly_stats
        
    except Exception as e:
        print(f"❌ 时间分析失败: {e}")
        return None, None

def calculate_portfolio_performance():
    """计算投资组合表现"""
    try:
        # 模拟持仓计算（基于订单记录）
        print(f"\n=== 投资组合表现分析 ===")
        
        # 读取当前持仓状态（从日志推断）
        df = pd.read_csv('logs/orders.csv')
        successful_orders = df[df['status'] != 'OrderStatus.Rejected'].copy()
        
        if len(successful_orders) == 0:
            print("⚠️ 没有找到成功的订单记录")
            return
        
        # 按股票计算净持仓
        portfolio = {}
        total_cost = 0
        
        for _, row in successful_orders.iterrows():
            symbol = row['symbol']
            side = row['side']
            quantity = row['quantity'] if pd.notna(row['quantity']) else 0
            price = row['price'] if pd.notna(row['price']) else 0
            
            if symbol not in portfolio:
                portfolio[symbol] = {'quantity': 0, 'cost': 0, 'trades': 0}
            
            portfolio[symbol]['trades'] += 1
            
            if 'Buy' in str(side):
                portfolio[symbol]['quantity'] += quantity
                portfolio[symbol]['cost'] += quantity * price
                total_cost += quantity * price
            elif 'Sell' in str(side):
                portfolio[symbol]['quantity'] -= quantity
                portfolio[symbol]['cost'] -= quantity * price
                total_cost -= quantity * price
        
        print(f"总投入资金: ${total_cost:,.2f}")
        print(f"\n当前持仓:")
        
        total_value = 0
        for symbol, data in portfolio.items():
            if data['quantity'] != 0:
                avg_cost = data['cost'] / data['quantity'] if data['quantity'] != 0 else 0
                print(f"  {symbol}: {data['quantity']} 股, 平均成本: ${avg_cost:.2f}, 交易次数: {data['trades']}")
                # 这里需要实时价格来计算当前价值
        
        return portfolio
        
    except Exception as e:
        print(f"❌ 投资组合分析失败: {e}")
        return None

def analyze_strategy_performance(df):
    """分析策略表现"""
    try:
        print(f"\n=== 策略表现分析 ===")
        
        # 按置信度分析（如果有的话）
        if 'signal_id' in df.columns:
            # 从signal_id中提取置信度信息（如果格式一致）
            confidence_analysis = defaultdict(list)
            
            for idx, row in df.iterrows():
                signal_id = row.get('signal_id', '')
                if pd.notna(signal_id) and 'confidence' in str(signal_id).lower():
                    # 这里可以根据实际signal_id格式解析置信度
                    pass
        
        # 买卖比例分析
        buy_orders = df[df['side'].str.contains('Buy', na=False)]
        sell_orders = df[df['side'].str.contains('Sell', na=False)]
        
        print(f"买单数量: {len(buy_orders)} ({len(buy_orders)/len(df)*100:.1f}%)")
        print(f"卖单数量: {len(sell_orders)} ({len(sell_orders)/len(df)*100:.1f}%)")
        
        # 价格区间分析
        if not df['price'].isna().all():
            df_with_price = df[df['price'].notna()]
            print(f"\n价格统计:")
            for symbol in df['symbol'].unique():
                symbol_data = df_with_price[df_with_price['symbol'] == symbol]
                if len(symbol_data) > 0:
                    print(f"  {symbol}: ${symbol_data['price'].min():.2f} - ${symbol_data['price'].max():.2f}")
        
    except Exception as e:
        print(f"❌ 策略分析失败: {e}")

def generate_recommendations():
    """生成优化建议"""
    print(f"\n=== 🎯 优化建议 ===")
    
    recommendations = [
        "1. 📈 提高订单成功率",
        "   - 优化价格策略，减少订单被拒绝",
        "   - 改进持仓检查逻辑",
        "   - 增强资金管理",
        "",
        "2. 🎛️ 策略模型优化",
        "   - 提高信号质量和置信度",
        "   - 优化买卖时机判断",
        "   - 加强风险控制",
        "",
        "3. 💰 资金使用效率",
        "   - 优化仓位分配",
        "   - 改进资金利用率",
        "   - 减少无效交易",
        "",
        "4. 🕒 时间优化",
        "   - 分析最佳交易时间段",
        "   - 避免市场波动期",
        "   - 优化信号生成频率"
    ]
    
    for rec in recommendations:
        print(rec)

def optimize_strategy_model():
    """优化策略模型"""
    print(f"\n=== 🔧 策略模型优化 ===")
    
    config = load_config()
    
    # 当前配置分析
    current_config = config.get('strategy', {})
    print(f"当前模型配置:")
    print(f"  - 回溯周期: {current_config.get('lookback_period', 'N/A')} 天")
    print(f"  - 信号间隔: {current_config.get('signal_interval', 'N/A')} 秒")
    print(f"  - 买入阈值: {current_config.get('signal_processing', {}).get('buy_threshold', 'N/A')}")
    print(f"  - 卖出阈值: {current_config.get('signal_processing', {}).get('sell_threshold', 'N/A')}")
    
    # 优化建议
    optimizations = {
        'strategy': {
            'lookback_period': 90,  # 增加到90天
            'signal_interval': 900,  # 15分钟
            'signal_processing': {
                'buy_threshold': 0.08,   # 提高买入阈值到8%
                'sell_threshold': -0.05, # 卖出阈值调整到-5%
                'enable_hold': True,
                'confidence_threshold': 0.15,  # 新增置信度阈值15%
                'min_signal_strength': 0.1     # 最小信号强度10%
            },
            'training': {
                'epochs': 300,
                'batch_size': 128,
                'test_size': 0.2,
                'validation_split': 0.15,
                'early_stopping_patience': 20,
                'features': [
                    'close', 'volume', 'high', 'low', 'turnover',
                    'rsi', 'macd', 'bollinger_bands'  # 添加技术指标
                ],
                'model_architecture': {
                    'lstm_units': [256, 128, 64],
                    'dropout_rate': 0.4,
                    'learning_rate': 0.0003,
                    'activation': 'tanh',
                    'recurrent_dropout': 0.3
                }
            }
        },
        'execution': {
            'max_position_size': 2000.0,  # 增加最大仓位
            'risk_control': {
                'position_pct': 1.5,        # 降低单笔交易比例到1.5%
                'daily_loss_pct': 3.0,      # 降低日亏损限制到3%
                'max_correlation': 0.7,     # 新增相关性控制
                'volatility_threshold': 0.25 # 波动率阈值
            },
            'order_tracking': {
                'check_interval': 30,        # 延长检查间隔
                'timeout': 90,               # 延长超时时间
                'max_pending_orders': 10,    # 降低最大挂单数
                'smart_pricing': True        # 启用智能定价
            }
        }
    }
    
    print(f"\n🎯 建议的优化配置:")
    for section, params in optimizations.items():
        print(f"\n[{section}]")
        for key, value in params.items():
            if isinstance(value, dict):
                print(f"  {key}:")
                for subkey, subvalue in value.items():
                    print(f"    {subkey}: {subvalue}")
            else:
                print(f"  {key}: {value}")
    
    return optimizations

def main():
    """主函数"""
    print("🚀 开始交易绩效分析...")
    print("=" * 60)
    
    # 分析订单数据
    df = analyze_orders()
    if df is not None:
        # 时间模式分析
        daily_stats, hourly_stats = analyze_by_time(df)
        
        # 投资组合分析
        portfolio = calculate_portfolio_performance()
        
        # 策略表现分析
        analyze_strategy_performance(df)
        
        # 生成建议
        generate_recommendations()
        
        # 模型优化
        optimizations = optimize_strategy_model()
        
        print(f"\n" + "=" * 60)
        print("📊 分析完成！")
        print("💡 建议根据以上分析结果优化策略参数")
        
        # 询问是否应用优化
        response = input("\n🤔 是否要应用建议的配置优化？(y/n): ")
        if response.lower() == 'y':
            apply_optimizations(optimizations)
    
    else:
        print("❌ 无法加载订单数据，请检查 logs/orders.csv 文件")

def apply_optimizations(optimizations):
    """应用优化配置"""
    try:
        # 备份当前配置
        import shutil
        shutil.copy('config.yaml', f'config.yaml.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}')
        print("✅ 已备份当前配置")
        
        # 读取当前配置
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # 应用优化
        for section, params in optimizations.items():
            if section not in config:
                config[section] = {}
            
            def update_nested_dict(target, source):
                for key, value in source.items():
                    if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                        update_nested_dict(target[key], value)
                    else:
                        target[key] = value
            
            update_nested_dict(config[section], params)
        
        # 保存优化后的配置
        with open('config.yaml', 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        
        print("✅ 配置优化已应用")
        print("🔄 请重启交易系统以使配置生效")
        
    except Exception as e:
        print(f"❌ 应用配置失败: {e}")

if __name__ == "__main__":
    main() 