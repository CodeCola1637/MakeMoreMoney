#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
交易监控仪表盘 - Flask Web 应用

提供：
- 交易记录查看（分页、筛选）
- 异常信号实时面板（机构信号、成交量异常）
- 持仓概览
- 系统状态
"""

import csv
import json
import os
import sys
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

app = Flask(__name__)

# ============================================================
# 路径配置
# ============================================================
ORDERS_CSV = os.path.join(PROJECT_ROOT, "logs", "orders.csv")
INSTITUTIONAL_CACHE = os.path.join(PROJECT_ROOT, "data_cache", "institutional", "tracker_cache.json")
ANOMALY_SIGNALS_JSON = os.path.join(PROJECT_ROOT, "data_cache", "signals", "anomaly_signals.json")
TRADING_LOG = os.path.join(PROJECT_ROOT, "trading_output.log")
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.yaml")


# ============================================================
# API 路由
# ============================================================

@app.route("/api/orders")
def api_orders():
    """交易记录 API（支持分页和筛选）"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    symbol_filter = request.args.get("symbol", "").strip()
    side_filter = request.args.get("side", "").strip()

    orders = _load_orders()

    if symbol_filter:
        orders = [o for o in orders if symbol_filter.upper() in o.get("symbol", "").upper()]
    if side_filter:
        orders = [o for o in orders if side_filter.lower() in o.get("side", "").lower()]

    total = len(orders)
    start = (page - 1) * per_page
    end = start + per_page

    return jsonify({
        "orders": orders[start:end],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    })


@app.route("/api/orders/stats")
def api_order_stats():
    """交易统计 API"""
    orders = _load_orders()

    total = len(orders)
    buys = sum(1 for o in orders if "buy" in o.get("side", "").lower())
    sells = sum(1 for o in orders if "sell" in o.get("side", "").lower())

    symbols = {}
    strategies = {}
    statuses = {}
    for o in orders:
        sym = o.get("symbol", "unknown")
        symbols[sym] = symbols.get(sym, 0) + 1
        strat = o.get("strategy", "unknown")
        strategies[strat] = strategies.get(strat, 0) + 1
        status = o.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1

    top_symbols = sorted(symbols.items(), key=lambda x: x[1], reverse=True)[:10]

    return jsonify({
        "total_orders": total,
        "buy_orders": buys,
        "sell_orders": sells,
        "top_symbols": top_symbols,
        "strategies": strategies,
        "statuses": statuses,
    })


@app.route("/api/institutional")
def api_institutional():
    """机构持仓和内部人交易数据 API"""
    data = _load_institutional_cache()
    if not data:
        return jsonify({"error": "no data"}), 404

    # 汇总机构热门持仓
    symbol_institutions = {}
    for inst, holdings in data.get("holdings", {}).items():
        for h in holdings:
            sym = h.get("symbol", "")
            if sym not in symbol_institutions:
                symbol_institutions[sym] = {"symbol": sym, "institutions": [], "total_shares": 0, "total_value_k": 0}
            symbol_institutions[sym]["institutions"].append(h.get("institution", inst))
            symbol_institutions[sym]["total_shares"] += h.get("shares", 0)
            symbol_institutions[sym]["total_value_k"] += h.get("value_thousands", 0)

    hot_stocks = sorted(symbol_institutions.values(), key=lambda x: len(x["institutions"]), reverse=True)[:20]
    for s in hot_stocks:
        s["institution_count"] = len(s["institutions"])
        s["institutions"] = s["institutions"][:5]

    # 内部人交易
    insider_trades = []
    for sym, txns in data.get("insider_trades", {}).items():
        for t in txns:
            insider_trades.append(t)
    insider_trades.sort(key=lambda x: x.get("filing_date", ""), reverse=True)

    return jsonify({
        "hot_stocks": hot_stocks,
        "insider_trades": insider_trades[:50],
        "last_13f_scan": data.get("last_13f_scan"),
        "last_insider_scan": data.get("last_insider_scan"),
    })


@app.route("/api/signals")
def api_signals():
    """异常信号 API（优先从 JSON 信号文件读取，回退到日志解析）"""
    signals = _load_anomaly_signals()
    log_signals = _extract_signals_from_log()
    # 合并：JSON 信号优先（结构化），日志信号补充
    combined = signals + log_signals
    # 按时间倒序
    combined.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return jsonify({"signals": combined[:200]})


@app.route("/api/system")
def api_system():
    """系统状态 API"""
    import subprocess
    # 检查交易进程
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python.*main.py"],
            capture_output=True, text=True, timeout=5
        )
        trading_running = result.returncode == 0
        trading_pid = result.stdout.strip().split("\n")[0] if trading_running else None
    except Exception:
        trading_running = False
        trading_pid = None

    # 最后日志时间
    last_log_time = None
    if os.path.exists(TRADING_LOG):
        try:
            with open(TRADING_LOG, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 2000))
                last_lines = f.read().decode("utf-8", errors="replace").strip().split("\n")
                for line in reversed(last_lines):
                    if line and line[0:4].isdigit():
                        last_log_time = line[:23]
                        break
        except Exception:
            pass

    return jsonify({
        "trading_running": trading_running,
        "trading_pid": trading_pid,
        "last_log_time": last_log_time,
        "orders_file_exists": os.path.exists(ORDERS_CSV),
        "institutional_cache_exists": os.path.exists(INSTITUTIONAL_CACHE),
    })


# ============================================================
# 主页面
# ============================================================

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


# ============================================================
# 数据加载
# ============================================================

def _load_orders():
    """加载交易记录，兼容两种 CSV 行格式"""
    if not os.path.exists(ORDERS_CSV):
        return []

    orders = []
    seen_ids = set()
    try:
        with open(ORDERS_CSV, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return []

            for row in reader:
                cols = len(row)
                clean = {}

                if cols >= 12:
                    # 标准12列: order_id,symbol,side,quantity,price,status,signal_id,signal_type,submitted_at,...
                    clean["order_id"] = row[0]
                    clean["symbol"] = row[1]
                    clean["side"] = row[2]
                    clean["quantity"] = row[3]
                    clean["price"] = row[4]
                    clean["status"] = row[5]
                    clean["signal_id"] = row[6]
                    clean["signal_type"] = row[7]
                    clean["submitted_at"] = row[8]
                elif cols == 8:
                    # 短格式: timestamp,order_id,symbol,side,quantity,price,status,extra
                    clean["submitted_at"] = row[0]
                    clean["order_id"] = row[1]
                    clean["symbol"] = row[2]
                    clean["side"] = row[3]
                    clean["quantity"] = row[4]
                    clean["price"] = row[5]
                    clean["status"] = row[6]
                    clean["signal_id"] = row[7] if len(row) > 7 else ""
                else:
                    continue

                # 去重（同一个 order_id 的 12 列行优先）
                oid = clean.get("order_id", "")
                if oid and oid in seen_ids:
                    continue
                if oid:
                    seen_ids.add(oid)

                # 标准化 side
                side = clean.get("side", "")
                if "Buy" in side:
                    clean["side_display"] = "BUY"
                    clean["side_class"] = "buy"
                elif "Sell" in side:
                    clean["side_display"] = "SELL"
                    clean["side_class"] = "sell"
                else:
                    clean["side_display"] = side
                    clean["side_class"] = ""

                # 标准化 status
                status = clean.get("status", "")
                if "Rejected" in status:
                    clean["status_display"] = "Rejected"
                    clean["status_class"] = "rejected"
                elif "Filled" in status or "FullyFilled" in status:
                    clean["status_display"] = "Filled"
                    clean["status_class"] = "filled"
                elif "NotReported" in status or "Submitted" in status or "New" in status:
                    clean["status_display"] = "Submitted"
                    clean["status_class"] = "submitted"
                elif "Cancelled" in status:
                    clean["status_display"] = "Cancelled"
                    clean["status_class"] = "cancelled"
                else:
                    clean["status_display"] = status
                    clean["status_class"] = ""

                # 提取策略名
                signal_id = clean.get("signal_id", "")
                if "|" in signal_id:
                    clean["strategy"] = signal_id.split("|")[0]
                elif clean.get("signal_type"):
                    clean["strategy"] = clean["signal_type"]
                else:
                    clean["strategy"] = ""

                orders.append(clean)
    except Exception:
        pass

    orders.reverse()
    return orders


def _load_institutional_cache():
    """加载机构跟踪缓存"""
    if not os.path.exists(INSTITUTIONAL_CACHE):
        return None
    try:
        with open(INSTITUTIONAL_CACHE, "r") as f:
            return json.load(f)
    except Exception:
        return None


ANOMALY_TYPE_MAP = {
    "volume_surge": {"type": "Volume Surge", "icon": "surge"},
    "volume_spike": {"type": "Volume Spike", "icon": "spike"},
    "block_trade": {"type": "Block Trade", "icon": "block"},
    "price_volume_divergence": {"type": "Price-Vol Divergence", "icon": "diverge"},
}


def _load_anomaly_signals():
    """从 JSON 信号文件加载异常信号"""
    if not os.path.exists(ANOMALY_SIGNALS_JSON):
        return []
    try:
        with open(ANOMALY_SIGNALS_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            return []

        signals = []
        for entry in raw:
            atype = entry.get("type", "")
            meta = ANOMALY_TYPE_MAP.get(atype, {"type": atype, "icon": "surge"})
            direction = entry.get("direction", "")
            signals.append({
                "type": meta["type"],
                "icon": meta["icon"],
                "symbol": entry.get("symbol", ""),
                "timestamp": entry.get("timestamp", "")[:19].replace("T", " "),
                "confidence": entry.get("confidence", 0),
                "price": entry.get("price", 0),
                "volume_ratio": entry.get("volume_ratio", 0),
                "direction": direction,
                "direction_label": {"buy": "主买", "sell": "主卖"}.get(direction, ""),
                "line": entry.get("details", ""),
                "source": "detector",
            })
        return signals
    except Exception:
        return []


def _extract_signals_from_log():
    """从交易日志提取最近的异常信号"""
    signals = []
    if not os.path.exists(TRADING_LOG):
        return signals

    try:
        with open(TRADING_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            # 读取最后 200KB
            f.seek(max(0, size - 200_000))
            content = f.read().decode("utf-8", errors="replace")

        for line in content.split("\n"):
            signal_entry = None

            if "[SURGE]" in line:
                signal_entry = {"type": "Volume Surge", "icon": "surge", "line": line}
            elif "[SPIKE]" in line:
                signal_entry = {"type": "Volume Spike", "icon": "spike", "line": line}
            elif "[BLOCK]" in line:
                signal_entry = {"type": "Block Trade", "icon": "block", "line": line}
            elif "[DIVERGE]" in line:
                signal_entry = {"type": "Price-Vol Divergence", "icon": "diverge", "line": line}
            elif "机构跟踪信号" in line or "🏦" in line and "信号" in line:
                signal_entry = {"type": "Institutional", "icon": "institutional", "line": line}
            elif "发现候选股票" in line:
                signal_entry = {"type": "Discovery", "icon": "discovery", "line": line}

            if signal_entry:
                # 提取时间戳
                if line[:4].isdigit():
                    signal_entry["timestamp"] = line[:19]
                # 提取股票代码
                for token in line.split():
                    if ".US" in token or ".HK" in token:
                        signal_entry["symbol"] = token.rstrip(",:")
                        break
                signals.append(signal_entry)

    except Exception:
        pass

    signals.reverse()
    return signals[:100]


# ============================================================
# HTML 前端（内嵌单文件，现代深色UI）
# ============================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MakeMoreMoney - Trading Dashboard</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #0f1117;
  --surface: #1a1d27;
  --surface2: #232733;
  --border: #2d3140;
  --text: #e4e6eb;
  --text2: #8b8fa3;
  --green: #00c896;
  --red: #ff4d6a;
  --blue: #4d8df7;
  --orange: #ff9f43;
  --purple: #a855f7;
  --yellow: #fbbf24;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
}

.container { max-width: 1400px; margin: 0 auto; padding: 0 20px; }

/* Header */
header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 16px 0;
  position: sticky;
  top: 0;
  z-index: 100;
}
header .inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
header h1 {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.5px;
}
header h1 span { color: var(--green); }
.status-dot {
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  animation: pulse 2s infinite;
}
.status-dot.online { background: var(--green); }
.status-dot.offline { background: var(--red); animation: none; }
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.header-status {
  font-size: 13px;
  color: var(--text2);
  display: flex;
  align-items: center;
  gap: 16px;
}

/* Tabs */
.tabs {
  display: flex;
  gap: 4px;
  background: var(--surface);
  padding: 6px;
  border-radius: 10px;
  margin: 20px 0;
}
.tab {
  padding: 10px 20px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  color: var(--text2);
  transition: all 0.2s;
  border: none;
  background: none;
}
.tab:hover { color: var(--text); background: var(--surface2); }
.tab.active { color: #fff; background: var(--blue); }

/* Cards */
.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.stat-card .label { font-size: 12px; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card .value { font-size: 28px; font-weight: 700; margin-top: 4px; }
.stat-card .sub { font-size: 12px; color: var(--text2); margin-top: 4px; }

/* Table */
.table-wrap {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.table-toolbar {
  display: flex;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
  align-items: center;
}
.table-toolbar input, .table-toolbar select {
  background: var(--surface2);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  outline: none;
}
.table-toolbar input:focus { border-color: var(--blue); }

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
thead th {
  text-align: left;
  padding: 12px 16px;
  font-weight: 600;
  color: var(--text2);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--surface);
}
tbody td {
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
tbody tr:hover { background: var(--surface2); }

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}
.badge.buy { background: rgba(0,200,150,0.15); color: var(--green); }
.badge.sell { background: rgba(255,77,106,0.15); color: var(--red); }
.badge.filled { background: rgba(0,200,150,0.15); color: var(--green); }
.badge.rejected { background: rgba(255,77,106,0.15); color: var(--red); }
.badge.submitted { background: rgba(77,141,247,0.15); color: var(--blue); }
.badge.cancelled { background: rgba(139,143,163,0.15); color: var(--text2); }

.pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  font-size: 13px;
  color: var(--text2);
}
.pagination button {
  background: var(--surface2);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.pagination button:disabled { opacity: 0.4; cursor: default; }
.pagination button:hover:not(:disabled) { background: var(--border); }

/* Signal cards */
.signal-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.signal-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 18px;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  transition: border-color 0.2s;
}
.signal-card:hover { border-color: var(--blue); }
.signal-icon {
  width: 36px; height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.signal-icon.surge { background: rgba(255,159,67,0.15); }
.signal-icon.spike { background: rgba(255,77,106,0.15); }
.signal-icon.block { background: rgba(168,85,247,0.15); }
.signal-icon.diverge { background: rgba(77,141,247,0.15); }
.signal-icon.institutional { background: rgba(0,200,150,0.15); }
.signal-icon.discovery { background: rgba(251,191,36,0.15); }
.signal-body { flex: 1; min-width: 0; }
.signal-body .sig-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.signal-body .sig-type { font-weight: 600; font-size: 13px; }
.signal-body .sig-symbol { color: var(--blue); font-weight: 600; font-size: 13px; }
.signal-body .sig-time { color: var(--text2); font-size: 12px; margin-left: auto; }
.signal-body .sig-detail { color: var(--text2); font-size: 12px; word-break: break-all; }

/* Institution table */
.inst-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
  margin-top: 16px;
}
.inst-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
}
.inst-card .sym { font-size: 16px; font-weight: 700; color: var(--blue); }
.inst-card .meta { font-size: 12px; color: var(--text2); margin-top: 6px; }
.inst-card .bar {
  height: 4px;
  background: var(--surface2);
  border-radius: 2px;
  margin-top: 10px;
  overflow: hidden;
}
.inst-card .bar-fill { height: 100%; background: var(--green); border-radius: 2px; }

/* Panel */
.panel { display: none; }
.panel.active { display: block; }

/* Responsive */
@media (max-width: 768px) {
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .table-toolbar { flex-direction: column; }
}
</style>
</head>
<body>

<header>
  <div class="container inner">
    <h1>Make<span>More</span>Money</h1>
    <div class="header-status">
      <span><span class="status-dot" id="statusDot"></span><span id="statusText">Checking...</span></span>
      <span id="lastUpdate" style="font-size:12px"></span>
    </div>
  </div>
</header>

<div class="container">
  <div class="tabs">
    <button class="tab active" onclick="switchTab('orders')">Trading Orders</button>
    <button class="tab" onclick="switchTab('signals')">Anomaly Signals</button>
    <button class="tab" onclick="switchTab('institutional')">Institutional</button>
  </div>

  <!-- Orders Panel -->
  <div class="panel active" id="panel-orders">
    <div class="stats-row" id="statsRow"></div>
    <div class="table-wrap">
      <div class="table-toolbar">
        <input type="text" id="filterSymbol" placeholder="Filter symbol..." oninput="loadOrders(1)">
        <select id="filterSide" onchange="loadOrders(1)">
          <option value="">All Sides</option>
          <option value="buy">Buy</option>
          <option value="sell">Sell</option>
        </select>
        <span style="margin-left:auto;color:var(--text2);font-size:13px" id="orderCount"></span>
      </div>
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Price</th>
              <th>Status</th>
              <th>Strategy</th>
              <th>Order ID</th>
            </tr>
          </thead>
          <tbody id="ordersBody"></tbody>
        </table>
      </div>
      <div class="pagination">
        <span id="pageInfo"></span>
        <div>
          <button onclick="prevPage()">&lt; Prev</button>
          <button onclick="nextPage()">Next &gt;</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Signals Panel -->
  <div class="panel" id="panel-signals">
    <div class="stats-row">
      <div class="stat-card">
        <div class="label">Anomaly Signals</div>
        <div class="value" id="signalCount">-</div>
        <div class="sub">From latest log</div>
      </div>
      <div class="stat-card">
        <div class="label">Volume Alerts</div>
        <div class="value" id="volumeAlerts">-</div>
        <div class="sub">Surge + Spike + Block</div>
      </div>
      <div class="stat-card">
        <div class="label">Institutional Signals</div>
        <div class="value" id="instSignals">-</div>
        <div class="sub">From SEC filings</div>
      </div>
      <div class="stat-card">
        <div class="label">Discovery Signals</div>
        <div class="value" id="discSignals">-</div>
        <div class="sub">Stock scanner</div>
      </div>
    </div>
    <div class="signal-list" id="signalList"></div>
  </div>

  <!-- Institutional Panel -->
  <div class="panel" id="panel-institutional">
    <div class="stats-row">
      <div class="stat-card">
        <div class="label">Last 13F Scan</div>
        <div class="value" style="font-size:16px" id="last13f">-</div>
      </div>
      <div class="stat-card">
        <div class="label">Last Insider Scan</div>
        <div class="value" style="font-size:16px" id="lastInsider">-</div>
      </div>
    </div>
    <h3 style="margin:20px 0 10px;font-size:16px">Hot Stocks (by institution count)</h3>
    <div class="inst-grid" id="instGrid"></div>
    <h3 style="margin:30px 0 10px;font-size:16px">Recent Insider Transactions</h3>
    <div class="table-wrap" style="margin-top:10px">
      <div style="overflow-x:auto">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Symbol</th>
              <th>Insider</th>
              <th>Role</th>
              <th>Type</th>
              <th>Shares</th>
              <th>Price</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody id="insiderBody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<script>
let currentPage = 1;
let totalPages = 1;

// Tab switching
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('panel-' + name).classList.add('active');

  if (name === 'orders') loadOrders(1);
  if (name === 'signals') loadSignals();
  if (name === 'institutional') loadInstitutional();
}

// System status
async function loadStatus() {
  try {
    const r = await fetch('/api/system');
    const d = await r.json();
    const dot = document.getElementById('statusDot');
    const txt = document.getElementById('statusText');
    if (d.trading_running) {
      dot.className = 'status-dot online';
      txt.textContent = 'System Online (PID: ' + d.trading_pid + ')';
    } else {
      dot.className = 'status-dot offline';
      txt.textContent = 'System Offline';
    }
    if (d.last_log_time) {
      document.getElementById('lastUpdate').textContent = 'Last log: ' + d.last_log_time;
    }
  } catch(e) {}
}

// Orders
async function loadOrders(page) {
  currentPage = page || 1;
  const sym = document.getElementById('filterSymbol').value;
  const side = document.getElementById('filterSide').value;
  const url = `/api/orders?page=${currentPage}&per_page=50&symbol=${sym}&side=${side}`;
  try {
    const r = await fetch(url);
    const d = await r.json();
    totalPages = d.total_pages;
    document.getElementById('orderCount').textContent = d.total + ' orders total';
    document.getElementById('pageInfo').textContent = `Page ${d.page} of ${d.total_pages}`;

    const tbody = document.getElementById('ordersBody');
    tbody.innerHTML = d.orders.map(o => `
      <tr>
        <td>${o.submitted_at || o.timestamp || ''}</td>
        <td><strong>${o.symbol || ''}</strong></td>
        <td><span class="badge ${o.side_class}">${o.side_display}</span></td>
        <td>${o.quantity || ''}</td>
        <td>$${parseFloat(o.price || 0).toFixed(2)}</td>
        <td><span class="badge ${o.status_class}">${o.status_display}</span></td>
        <td>${o.strategy || ''}</td>
        <td style="font-size:11px;color:var(--text2)">${(o.order_id || '').slice(-8)}</td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

async function loadOrderStats() {
  try {
    const r = await fetch('/api/orders/stats');
    const d = await r.json();
    document.getElementById('statsRow').innerHTML = `
      <div class="stat-card">
        <div class="label">Total Orders</div>
        <div class="value">${d.total_orders.toLocaleString()}</div>
      </div>
      <div class="stat-card">
        <div class="label">Buy Orders</div>
        <div class="value" style="color:var(--green)">${d.buy_orders.toLocaleString()}</div>
      </div>
      <div class="stat-card">
        <div class="label">Sell Orders</div>
        <div class="value" style="color:var(--red)">${d.sell_orders.toLocaleString()}</div>
      </div>
      <div class="stat-card">
        <div class="label">Top Symbol</div>
        <div class="value" style="font-size:18px;color:var(--blue)">${d.top_symbols.length ? d.top_symbols[0][0] : '-'}</div>
        <div class="sub">${d.top_symbols.length ? d.top_symbols[0][1] + ' orders' : ''}</div>
      </div>
    `;
  } catch(e) {}
}

function prevPage() { if (currentPage > 1) loadOrders(currentPage - 1); }
function nextPage() { if (currentPage < totalPages) loadOrders(currentPage + 1); }

// Signals
const SIGNAL_ICONS = {
  'surge': '⚡', 'spike': '🔺', 'block': '🟪',
  'diverge': '🔄', 'institutional': '🏦', 'discovery': '🔍'
};

async function loadSignals() {
  try {
    const r = await fetch('/api/signals');
    const d = await r.json();
    const sigs = d.signals;

    document.getElementById('signalCount').textContent = sigs.length;
    document.getElementById('volumeAlerts').textContent =
      sigs.filter(s => ['Volume Surge','Volume Spike','Block Trade'].includes(s.type)).length;
    document.getElementById('instSignals').textContent =
      sigs.filter(s => s.type === 'Institutional').length;
    document.getElementById('discSignals').textContent =
      sigs.filter(s => s.type === 'Discovery').length;

    document.getElementById('signalList').innerHTML = sigs.slice(0, 80).map(s => {
      const conf = s.confidence ? `<span class="badge ${s.confidence > 0.7 ? 'filled' : 'submitted'}" style="margin-left:6px">${(s.confidence*100).toFixed(0)}%</span>` : '';
      const ratio = s.volume_ratio ? `<span style="color:var(--orange);font-size:12px;margin-left:8px">${s.volume_ratio}x vol</span>` : '';
      const price = s.price ? `<span style="color:var(--text2);font-size:12px;margin-left:8px">$${parseFloat(s.price).toFixed(2)}</span>` : '';
      const dir = s.direction_label ? `<span class="badge ${s.direction === 'buy' ? 'buy' : 'sell'}" style="margin-left:6px">${s.direction_label}</span>` : '';
      const detail = s.line ? (s.source === 'detector' ? s.line : s.line.slice(s.line.indexOf('] ') + 2 || 30)) : '';
      return `
      <div class="signal-card">
        <div class="signal-icon ${s.icon}">${SIGNAL_ICONS[s.icon] || '📊'}</div>
        <div class="signal-body">
          <div class="sig-header">
            <span class="sig-type">${s.type}</span>
            ${s.symbol ? '<span class="sig-symbol">' + s.symbol + '</span>' : ''}
            ${dir}${conf}${ratio}${price}
            <span class="sig-time">${s.timestamp || ''}</span>
          </div>
          <div class="sig-detail">${detail}</div>
        </div>
      </div>`;
    }).join('') || '<p style="color:var(--text2);padding:40px;text-align:center">No signals detected yet</p>';
  } catch(e) { console.error(e); }
}

// Institutional
async function loadInstitutional() {
  try {
    const r = await fetch('/api/institutional');
    if (!r.ok) {
      document.getElementById('instGrid').innerHTML = '<p style="color:var(--text2)">No institutional data available</p>';
      return;
    }
    const d = await r.json();

    document.getElementById('last13f').textContent = d.last_13f_scan ? d.last_13f_scan.slice(0,16) : '-';
    document.getElementById('lastInsider').textContent = d.last_insider_scan ? d.last_insider_scan.slice(0,16) : '-';

    const maxInst = d.hot_stocks.length ? d.hot_stocks[0].institution_count : 1;
    document.getElementById('instGrid').innerHTML = d.hot_stocks.map(s => `
      <div class="inst-card">
        <div class="sym">${s.symbol}</div>
        <div class="meta">${s.institution_count} institutions · ${(s.total_shares/1e6).toFixed(1)}M shares · $${(s.total_value_k/1e6).toFixed(0)}B</div>
        <div class="bar"><div class="bar-fill" style="width:${(s.institution_count/maxInst*100).toFixed(0)}%"></div></div>
      </div>
    `).join('');

    document.getElementById('insiderBody').innerHTML = d.insider_trades.map(t => {
      const isBuy = t.transaction_type === 'P';
      return `<tr>
        <td>${t.filing_date || ''}</td>
        <td><strong>${t.symbol || ''}</strong></td>
        <td>${t.insider_name || ''}</td>
        <td style="font-size:12px">${t.role || ''}</td>
        <td><span class="badge ${isBuy ? 'buy' : 'sell'}">${isBuy ? 'BUY' : 'SELL'}</span></td>
        <td>${(t.shares||0).toLocaleString()}</td>
        <td>$${parseFloat(t.price||0).toFixed(2)}</td>
        <td>$${(t.total_value||0).toLocaleString()}</td>
      </tr>`;
    }).join('');
  } catch(e) { console.error(e); }
}

// Init
loadStatus();
loadOrders(1);
loadOrderStats();
setInterval(loadStatus, 30000);
setInterval(() => { if (document.getElementById('panel-signals').classList.contains('active')) loadSignals(); }, 60000);
</script>

</body>
</html>
"""


# ============================================================
# 启动入口
# ============================================================

def start_dashboard(host="0.0.0.0", port=8888):
    """启动仪表盘服务"""
    print(f"Dashboard starting at http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Trading Dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()
    start_dashboard(args.host, args.port)
