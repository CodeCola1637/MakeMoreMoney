#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
import subprocess
import re
from datetime import datetime, timedelta
from collections import defaultdict, deque
import os

class TradingMonitor:
    """交易优化监控器"""
    
    def __init__(self):
        self.start_time = datetime.now()
        self.signal_stats = defaultdict(int)
        self.recent_signals = deque(maxlen=50)  # 保存最近50个信号
        self.last_log_position = 0
        self.optimization_time = None
        
        # 查找优化时间
        self._find_optimization_time()
    
    def _find_optimization_time(self):
        """查找策略优化应用的时间"""
        try:
            # 查找配置备份文件
            backup_files = [f for f in os.listdir('.') if f.startswith('config.yaml.backup.2025')]
            if backup_files:
                latest_backup = sorted(backup_files)[-1]
                timestamp_str = latest_backup.split('.')[-1]
                self.optimization_time = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                print(f"📅 策略优化时间: {self.optimization_time.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            print(f"⚠️ 无法确定优化时间: {e}")
    
    def parse_log_line(self, line):
        """解析日志行"""
        try:
            # 提取时间戳
            timestamp_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            if not timestamp_match:
                return None
            
            timestamp = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S')
            
            # 只分析优化后的数据
            if self.optimization_time and timestamp < self.optimization_time:
                return None
            
            signal_info = {}
            signal_info['timestamp'] = timestamp
            
            # 解析交易信号
            if '收到交易信号:' in line:
                # 例如: 收到交易信号: TSLA.US BUY 10股 @ 297.290
                signal_match = re.search(r'收到交易信号:\s+(\w+\.\w+)\s+(\w+)\s+(\d+)股\s+@\s+([\d.]+)', line)
                if signal_match:
                    signal_info.update({
                        'type': 'signal',
                        'symbol': signal_match.group(1),
                        'action': signal_match.group(2),
                        'quantity': int(signal_match.group(3)),
                        'price': float(signal_match.group(4))
                    })
                    return signal_info
            
            # 解析模型预测
            elif '模型预测结果详情:' in line:
                pred_match = re.search(r'模型预测结果详情:\s+(\w+\.\w+),\s+预测值:\s+([-\d.]+)', line)
                if pred_match:
                    signal_info.update({
                        'type': 'prediction',
                        'symbol': pred_match.group(1),
                        'prediction': float(pred_match.group(2))
                    })
                    return signal_info
            
            # 解析信号生成
            elif '生成' in line and ('买入信号' in line or '卖出信号' in line or '持有信号' in line):
                if '买入信号' in line:
                    action = 'BUY'
                elif '卖出信号' in line:
                    action = 'SELL'
                else:
                    action = 'HOLD'
                
                symbol_match = re.search(r'(\w+\.\w+)', line)
                pred_match = re.search(r'预测值\s+([-\d.]+)', line)
                threshold_match = re.search(r'阈值\s+([-\d.]+)', line)
                
                if symbol_match:
                    signal_info.update({
                        'type': 'signal_generation',
                        'symbol': symbol_match.group(1),
                        'action': action,
                        'prediction': float(pred_match.group(1)) if pred_match else 0,
                        'threshold': float(threshold_match.group(1)) if threshold_match else 0
                    })
                    return signal_info
            
            # 解析订单状态
            elif '订单已提交:' in line:
                order_match = re.search(r'OrderResult\([^,]+,\s+([^,]+),\s+OrderSide\.(\w+).*status=OrderStatus\.(\w+)', line)
                if order_match:
                    signal_info.update({
                        'type': 'order',
                        'symbol': order_match.group(1),
                        'side': order_match.group(2),
                        'status': order_match.group(3)
                    })
                    return signal_info
            
        except Exception as e:
            pass
        
        return None
    
    def get_new_log_entries(self):
        """获取新的日志条目"""
        try:
            with open('logs/trading.log', 'r', encoding='utf-8') as f:
                f.seek(self.last_log_position)
                new_lines = f.readlines()
                self.last_log_position = f.tell()
                return new_lines
        except Exception as e:
            return []
    
    def analyze_signals(self, parsed_signals):
        """分析信号数据"""
        for signal in parsed_signals:
            if signal['type'] == 'signal_generation':
                self.signal_stats[f"{signal['action']}_signals"] += 1
                self.recent_signals.append(signal)
            elif signal['type'] == 'order':
                self.signal_stats[f"orders_{signal['status'].lower()}"] += 1
    
    def print_statistics(self):
        """打印统计信息"""
        current_time = datetime.now()
        runtime = current_time - self.start_time
        
        print(f"\n{'='*80}")
        print(f"🎯 交易策略优化实时监控")
        print(f"{'='*80}")
        print(f"📊 监控时间: {runtime}")
        if self.optimization_time:
            opt_runtime = current_time - self.optimization_time
            print(f"⏱️ 优化后运行: {opt_runtime}")
        print(f"🕐 当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 信号统计
        print(f"\n📈 信号生成统计:")
        total_signals = self.signal_stats.get('BUY_signals', 0) + self.signal_stats.get('SELL_signals', 0) + self.signal_stats.get('HOLD_signals', 0)
        
        if total_signals > 0:
            buy_pct = (self.signal_stats.get('BUY_signals', 0) / total_signals) * 100
            sell_pct = (self.signal_stats.get('SELL_signals', 0) / total_signals) * 100
            hold_pct = (self.signal_stats.get('HOLD_signals', 0) / total_signals) * 100
            
            print(f"   🟢 买入信号: {self.signal_stats.get('BUY_signals', 0)} ({buy_pct:.1f}%)")
            print(f"   🔴 卖出信号: {self.signal_stats.get('SELL_signals', 0)} ({sell_pct:.1f}%)")
            print(f"   🟡 持有信号: {self.signal_stats.get('HOLD_signals', 0)} ({hold_pct:.1f}%)")
            print(f"   📊 总信号数: {total_signals}")
            
            # 计算活跃度（非HOLD信号比例）
            active_signals = self.signal_stats.get('BUY_signals', 0) + self.signal_stats.get('SELL_signals', 0)
            activity_rate = (active_signals / total_signals) * 100 if total_signals > 0 else 0
            
            if activity_rate < 10:
                activity_status = "❌ 活跃度过低"
            elif activity_rate > 50:
                activity_status = "⚠️ 活跃度过高"
            else:
                activity_status = "✅ 活跃度适中"
            
            print(f"   🎯 信号活跃度: {activity_rate:.1f}% ({activity_status})")
        else:
            print(f"   ⏳ 暂无信号数据")
        
        # 订单统计
        print(f"\n💼 订单执行统计:")
        total_orders = sum(v for k, v in self.signal_stats.items() if k.startswith('orders_'))
        if total_orders > 0:
            for status in ['submitted', 'filled', 'rejected', 'cancelled']:
                count = self.signal_stats.get(f'orders_{status}', 0)
                pct = (count / total_orders) * 100
                status_emoji = {'submitted': '📤', 'filled': '✅', 'rejected': '❌', 'cancelled': '⏹️'}
                print(f"   {status_emoji.get(status, '📊')} {status.title()}: {count} ({pct:.1f}%)")
        else:
            print(f"   ⏳ 暂无订单数据")
        
        # 最近信号详情
        if self.recent_signals:
            print(f"\n🔍 最近5个信号:")
            for signal in list(self.recent_signals)[-5:]:
                time_str = signal['timestamp'].strftime('%H:%M:%S')
                action_emoji = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '🟡'}
                print(f"   {time_str} {action_emoji.get(signal['action'], '📊')} {signal['symbol']} {signal['action']} (预测: {signal['prediction']:.4f})")
        
        # 优化效果评估
        print(f"\n🎯 优化效果评估:")
        if total_signals >= 10:  # 有足够数据进行评估
            if activity_rate > 20:  # 比原来的5%有显著提升
                print(f"   ✅ 信号活跃度大幅提升: {activity_rate:.1f}% (目标: >20%)")
            else:
                print(f"   ⚠️ 信号活跃度仍需提升: {activity_rate:.1f}% (目标: >20%)")
            
            if buy_pct > 15 and sell_pct > 10:  # 买卖信号都有合理比例
                print(f"   ✅ 买卖信号分布均衡")
            else:
                print(f"   ⚠️ 信号分布可能需要调整")
        else:
            print(f"   ⏳ 数据收集中，需要更多样本进行评估")
    
    def run_monitoring(self):
        """运行监控"""
        print(f"🚀 开始监控交易策略优化效果...")
        print(f"💡 按 Ctrl+C 停止监控")
        
        try:
            while True:
                # 获取新的日志条目
                new_lines = self.get_new_log_entries()
                
                # 解析新的信号
                parsed_signals = []
                for line in new_lines:
                    parsed = self.parse_log_line(line)
                    if parsed:
                        parsed_signals.append(parsed)
                
                # 分析信号
                if parsed_signals:
                    self.analyze_signals(parsed_signals)
                
                # 每30秒打印一次统计
                self.print_statistics()
                
                time.sleep(30)
                
        except KeyboardInterrupt:
            print(f"\n\n🛑 监控已停止")
            self.print_statistics()
            print(f"\n💡 监控总结: 请根据上述数据评估优化效果")

def main():
    monitor = TradingMonitor()
    monitor.run_monitoring()

if __name__ == "__main__":
    main() 