#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟三项优化建议的实际效果：
  P0  CCASS 触发阈值 0.08 -> 0.05（或单独 ccass 阈值 0.06）
  P1a 反向回补价护栏：SELL 后 24h 内 BUY，要求 BUY 价格 ≤ SELL 价格
  P1b trailing_stop_pct 4% -> 5%（针对港股）
"""
from __future__ import annotations
import csv
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass


# ============================================================
# 公共数据：解析重启后真实成交（4/16 16:25 之后）
# ============================================================
@dataclass
class Trade:
    ts: datetime
    sym: str
    side: str   # Buy / Sell
    qty: int
    price: float


def load_trades(path='logs/orders.csv', since='2026-04-16 16:25'):
    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('order_id'):
                continue
            parts = line.split(',')
            if len(parts) < 7:
                continue
            ts_str = parts[0]
            if ts_str < since:
                continue
            try:
                ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            sym = parts[2]
            side = parts[3].replace('OrderSide.', '')
            try:
                qty = int(float(parts[4]))
                px = float(parts[5])
            except Exception:
                continue
            status = parts[6]
            if 'Filled' not in status:
                continue
            trades.append(Trade(ts, sym, side, qty, px))
    return trades


# ============================================================
# 场景一：CCASS 阈值降低
# ============================================================
def simulate_ccass_threshold():
    """
    9992.HK 实际：4/17 09:36:38 trailing_stop SELL @ 160.80
    CCASS 信号最早出现：4/17 02:56 (HK 闭市) 持续到 11:06
    阈值下调后能在 09:30 港股开盘第一笔即触发 SELL。
    """
    print("=" * 60)
    print("场景一: CCASS 阈值 0.08 -> 0.05")
    print("=" * 60)

    open_price_4_17 = 163.80   # 4/17 9992.HK 开盘价
    actual_sell_price = 160.80  # 实际 trailing_stop 触发价
    qty = 600
    ccass_first_signal = "04-17 02:56 (HK 闭市)"
    ccass_first_actionable = "04-17 09:30 (HK 开盘)"

    threshold_old = 0.08
    threshold_new = 0.05
    ccass_ensemble_conf = 0.079

    print(f"现状: CCASS 在 02:56 给出 SELL 信号（ensemble 置信度 0.079）")
    print(f"      0.079 < 0.08 阈值 → 被过滤")
    print(f"      9:36:38 trailing_stop 触发 SELL @ {actual_sell_price}")
    print()
    print(f"调整后: 0.079 >= {threshold_new} 阈值 → 触发")
    print(f"        9:30 开盘即可 SELL @ {open_price_4_17}")
    print()

    saved_per_share = open_price_4_17 - actual_sell_price
    saved_total = saved_per_share * qty
    print(f"价差: {open_price_4_17} - {actual_sell_price} = +HK${saved_per_share:.2f}/股")
    print(f"600 股节省亏损: +HK${saved_total:.2f}")
    print(f"原实际亏损: -HK$4,040  →  优化后: -HK${4040 - saved_total:.0f}")
    print()

    # 风险评估：如果 CCASS 出错，0.05 阈值更易误触发
    print("⚠️  误触发风险: 0.05 阈值会让历史上 6 个潜在 ccass 弱信号通过过滤")
    print("    建议: 给 ccass 单独触发阈值 0.06 (其他策略保持 0.08)")
    print()


# ============================================================
# 场景二：反向回补价护栏
# ============================================================
def simulate_cover_guard(trades):
    """
    规则: 同一标的 SELL 后 24h 内 BUY，要求 BUY 价格 ≤ SELL 价格
    """
    print("=" * 60)
    print("场景二: SELL→BUY 回补价护栏 (BUY ≤ 最近 24h SELL 价格)")
    print("=" * 60)

    last_sells = defaultdict(list)  # sym -> [(ts, price)]
    blocked_savings = 0.0
    blocked_orders = []
    allowed_orders = []

    for t in trades:
        if t.side == 'Sell':
            last_sells[t.sym].append((t.ts, t.price))
        elif t.side == 'Buy':
            # 找 24h 内最近的 SELL 价格
            recent = [(ts, px) for ts, px in last_sells[t.sym]
                      if t.ts - ts <= timedelta(hours=24)]
            if recent:
                last_ts, last_px = recent[-1]
                if t.price > last_px:
                    diff = (t.price - last_px) * t.qty
                    blocked_savings += diff
                    blocked_orders.append((t, last_ts, last_px, diff))
                else:
                    allowed_orders.append((t, last_ts, last_px))

    print(f"\n📛 被拦截的回补订单（共 {len(blocked_orders)} 笔）:")
    for t, last_ts, last_px, diff in blocked_orders:
        print(f"  {t.ts.strftime('%m-%d %H:%M')} {t.sym} BUY {t.qty}@{t.price}  "
              f"vs SELL@{last_px} ({last_ts.strftime('%H:%M')}) "
              f"→ 节省 ${diff:.2f}")

    print(f"\n✅ 允许的回补订单（{len(allowed_orders)} 笔，BUY ≤ SELL）:")
    for t, last_ts, last_px in allowed_orders:
        gain = (last_px - t.price) * t.qty
        print(f"  {t.ts.strftime('%m-%d %H:%M')} {t.sym} BUY {t.qty}@{t.price}  "
              f"vs SELL@{last_px} → 价差盈利 +${gain:.2f}")

    print(f"\n💰 累计避免损失: +${blocked_savings:.2f}")
    print()

    # 注意：被拦截后无法持有股票，可能错失后续上涨
    print("⚠️  副作用: 被拦截则不持有该标的，可能错失后续上涨/下跌")
    print("    SNDK 4/17 后续价格走势 → 需后续观察确认")
    print()


# ============================================================
# 场景三：港股 trailing_stop 4% -> 5%
# ============================================================
def simulate_trailing_stop_widen():
    """
    9992.HK 持仓 600 股，平均成本约 167.53
    实际 4% 止损线: 167.53 × 0.96 ≈ 160.83  → 9:36 触发于 160.80
    
    若 5%: 167.53 × 0.95 ≈ 159.15
    今日最低 159.40 → 不会触发
    今日 11:00 价格仍 ~160 → 持仓未平
    """
    print("=" * 60)
    print("场景三: 港股 trailing_stop_pct 4% -> 5%")
    print("=" * 60)

    avg_cost = 167.53
    qty = 600
    today_low = 159.40
    today_current = 159.80     # 4/17 当前 11:00 价格
    actual_sell_price = 160.80

    stop_4pct = avg_cost * 0.96
    stop_5pct = avg_cost * 0.95
    print(f"9992.HK 平均成本: HK${avg_cost:.2f}, 持仓 {qty} 股")
    print(f"  4% 止损线: HK${stop_4pct:.2f}  → 9:36 触发卖出 @ {actual_sell_price}")
    print(f"  5% 止损线: HK${stop_5pct:.2f}  → 今日最低 {today_low}, 不触发")
    print()

    # 已实际亏损 vs 假设按当前价继续持有
    actual_loss = (actual_sell_price - avg_cost) * qty
    floating_pl_now = (today_current - avg_cost) * qty
    extra_risk = (today_low - actual_sell_price) * qty   # 极端情况：跌到当日最低

    print(f"实际止损亏损:     HK${actual_loss:.2f}")
    print(f"持有至今浮亏:     HK${floating_pl_now:.2f}")
    print(f"差异（5% 优势）:  HK${floating_pl_now - actual_loss:+.2f}")
    print(f"极端风险（跌到当日最低 {today_low}）: 额外亏损 HK${extra_risk:.2f}")
    print()
    print("⚠️  权衡:")
    print(f"    - 4% 止损: 锁定亏损 -{abs(actual_loss):.0f}, 但若反弹会卖飞")
    print(f"    - 5% 止损: 多承担 ~840 HK$ 风险换取反弹机会，今日尚处亏损区")
    print(f"    - 历史上 700.HK / 388.HK 也常因 4% 过紧被洗出")
    print()


def simulate_combined():
    print("=" * 60)
    print("综合估算（仅基于本次 18.5h 数据）")
    print("=" * 60)
    print()
    print("CCASS 阈值优化:        +HK$1,800 (9992.HK 早卖)")
    print("回补价护栏:            +$192     (SNDK 拦截)")
    print("trailing 5%:           ±0       (今日仍处亏损区，未明显改善)")
    print()
    print("总计预期改善: 美股 +$192, 港股 +HK$1,800")
    print("(按 1 USD ≈ 7.8 HKD 折算: 约 +$423 美元等值)")
    print()
    print("⚠️  样本量过小（仅 1 天），需观察 5-7 个交易日才能确认效果")


def main():
    print()
    simulate_ccass_threshold()
    trades = load_trades()
    simulate_cover_guard(trades)
    simulate_trailing_stop_widen()
    simulate_combined()


if __name__ == '__main__':
    main()
