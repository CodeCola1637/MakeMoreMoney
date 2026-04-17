#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对港股做长期 trailing_stop 4% vs 5% 回测
模拟规则：每周一开盘买入，周五收盘卖出（或触发止损卖）
观察两种止损率的胜率/总收益差异
"""
import csv
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Bar:
    ts: datetime
    o: float
    h: float
    l: float
    c: float


def load_bars(symbol):
    path = f'data_cache/{symbol}_Day_NoAdjust.csv'
    bars = []
    with open(path) as f:
        next(f)
        for line in f:
            parts = line.strip().split(',')
            ts_str = parts[0].split(' ')[0]
            ts = datetime.strptime(ts_str, '%Y-%m-%d')
            bars.append(Bar(ts, float(parts[1]), float(parts[2]),
                            float(parts[3]), float(parts[4])))
    return bars


def backtest_trailing_stop(bars, stop_pct: float,
                           profit_step_pct: float = 1.5):
    """
    简化模型：买入后启用 trailing stop
    - 入场：每个周一/或前一交易日为新一周开始时的开盘价
    - trailing: 当价格 >= entry × (1 + profit_step_pct/100 × n) 时,
                止损线 = price × (1 - stop_pct/100)
    - 退出：触发止损 OR 周五收盘
    """
    trades = []
    i = 0
    n = len(bars)

    while i < n - 1:
        entry = bars[i].o
        if entry <= 0:
            i += 1
            continue
        stop = entry * (1 - stop_pct / 100)
        max_high = entry
        exit_price = None
        exit_reason = None
        j = i

        # 最多持有 5 个交易日
        while j < min(i + 5, n):
            bar = bars[j]
            # 先看是否触发止损（用当日 low 估计）
            if bar.l <= stop and j > i:  # 同一日不立即止损
                exit_price = stop
                exit_reason = 'stop'
                break
            # 更新移动止损
            if bar.h > max_high:
                max_high = bar.h
                # 计算可用 ratchet 步数
                ratchet_pct = (max_high / entry - 1) * 100
                steps = int(ratchet_pct / profit_step_pct)
                if steps > 0:
                    new_stop = max_high * (1 - stop_pct / 100)
                    if new_stop > stop:
                        stop = new_stop
            j += 1

        if exit_price is None:
            exit_price = bars[min(i + 4, n - 1)].c
            exit_reason = 'timeout'

        ret = (exit_price - entry) / entry
        trades.append({
            'entry_ts': bars[i].ts.strftime('%Y-%m-%d'),
            'entry': entry,
            'exit': exit_price,
            'ret': ret,
            'reason': exit_reason,
        })
        i += 5  # 跳到下一周

    return trades


def summarize(trades, label):
    if not trades:
        print(f"{label}: 无交易")
        return
    wins = [t for t in trades if t['ret'] > 0]
    losses = [t for t in trades if t['ret'] <= 0]
    total_ret = sum(t['ret'] for t in trades) * 100
    avg_ret = total_ret / len(trades)
    win_rate = len(wins) / len(trades) * 100
    stop_count = sum(1 for t in trades if t['reason'] == 'stop')
    avg_loss = (sum(t['ret'] for t in losses) / len(losses) * 100) if losses else 0
    avg_win = (sum(t['ret'] for t in wins) / len(wins) * 100) if wins else 0

    print(f"\n  {label}:")
    print(f"    交易数:   {len(trades)}")
    print(f"    胜率:     {win_rate:.1f}%  ({len(wins)} 胜 / {len(losses)} 负)")
    print(f"    总收益:   {total_ret:+.2f}%   平均 {avg_ret:+.2f}%/笔")
    print(f"    平均盈:   {avg_win:+.2f}%   平均亏: {avg_loss:.2f}%")
    print(f"    止损触发: {stop_count} 笔 ({stop_count/len(trades)*100:.0f}%)")


def compare(symbol: str):
    print(f"\n{'=' * 60}")
    print(f"  标的: {symbol}")
    print(f"{'=' * 60}")
    bars = load_bars(symbol)
    print(f"  数据范围: {bars[0].ts.date()} - {bars[-1].ts.date()} ({len(bars)} 根日 K)")

    trades_4 = backtest_trailing_stop(bars, stop_pct=4.0, profit_step_pct=1.5)
    trades_5 = backtest_trailing_stop(bars, stop_pct=5.0, profit_step_pct=1.5)

    summarize(trades_4, "4% 止损")
    summarize(trades_5, "5% 止损")

    if trades_4 and trades_5:
        diff = sum(t['ret'] for t in trades_5) - sum(t['ret'] for t in trades_4)
        print(f"\n  → 5% vs 4% 总收益差: {diff*100:+.2f}%")


def main():
    print("\n港股 trailing_stop 4% vs 5% 长期对比模拟")
    print("（简化模型：每周开盘买，5 日内退出，止损率独立测试）")

    for sym in ['9992.HK', '700.HK', '388.HK', '9988.HK', '941.HK']:
        try:
            compare(sym)
        except FileNotFoundError:
            print(f"\n跳过 {sym}（无历史数据）")


if __name__ == '__main__':
    main()
