"""
Real-time web dashboard for the paper trading bot.
Access at http://YOUR_VPS_IP:8080
"""
import time, json
from flask import Flask, jsonify, render_template_string
import db
from config import DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_REFRESH_SEC, PAPER_CAPITAL_USD

app = Flask(__name__)

# shared state injected by main.py
_strategy_engine = None

def set_strategy(engine):
    global _strategy_engine
    _strategy_engine = engine

# ── HTML Dashboard ───────────────────────────────────────────────────────────
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ETH Paper Trading Bot</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e; --green: #3fb950;
    --red: #f85149; --amber: #d29922; --blue: #58a6ff;
    --purple: #bc8cff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace; font-size: 14px; }
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; justify-content: space-between; align-items: center; }
  header h1 { font-size: 16px; font-weight: 600; color: var(--blue); }
  .live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); display: inline-block; margin-right: 6px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .container { max-width: 1400px; margin: 0 auto; padding: 20px 24px; }
  .metrics-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .metric { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .metric-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 6px; }
  .metric-val { font-size: 22px; font-weight: 600; }
  .green { color: var(--green); } .red { color: var(--red); } .amber { color: var(--amber); } .blue { color: var(--blue); }
  .section { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 20px; }
  .section-header { padding: 12px 16px; border-bottom: 1px solid var(--border); font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; display: flex; justify-content: space-between; align-items: center; }
  table { width: 100%; border-collapse: collapse; }
  th { padding: 10px 14px; text-align: left; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; border-bottom: 1px solid var(--border); font-weight: 500; }
  td { padding: 10px 14px; border-bottom: 1px solid var(--border); font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,.02); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 500; }
  .badge-open    { background: rgba(88,166,255,.15); color: var(--blue); }
  .badge-tp      { background: rgba(63,185,80,.15);  color: var(--green); }
  .badge-sl      { background: rgba(248,81,73,.15);  color: var(--red); }
  .badge-expiry  { background: rgba(210,153,34,.15); color: var(--amber); }
  .mono { font-family: monospace; font-size: 12px; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .hedge-log { max-height: 220px; overflow-y: auto; padding: 12px 16px; }
  .hedge-item { font-size: 12px; color: var(--muted); padding: 3px 0; border-bottom: 1px solid var(--border); }
  .hedge-item:last-child { border-bottom: none; }
  .no-data { padding: 40px; text-align: center; color: var(--muted); font-size: 13px; }
  .tag { background: rgba(188,140,255,.12); color: var(--purple); border-radius: 4px; padding: 1px 6px; font-size: 11px; }
  #last-update { font-size: 11px; color: var(--muted); }
  .chart-placeholder { height: 180px; display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 12px; border-top: 1px solid var(--border); }
  canvas { max-height: 180px; }
</style>
</head>
<body>
<header>
  <h1><span class="live-dot"></span>ETH Paper Bot — Strangle</h1>
  <span id="last-update">–</span>
</header>
<div class="container">

  <!-- KPI row -->
  <div class="metrics-row" id="kpi-row">
    <div class="metric"><div class="metric-label">ETH Price</div><div class="metric-val blue" id="eth-price">–</div></div>
    <div class="metric"><div class="metric-label">DVOL</div><div class="metric-val" id="dvol">–</div></div>
    <div class="metric"><div class="metric-label">Paper Capital</div><div class="metric-val">${{ "{:,.0f}".format(capital) }}</div></div>
    <div class="metric"><div class="metric-label">Total P&L</div><div class="metric-val" id="total-pnl">–</div></div>
    <div class="metric"><div class="metric-label">Win Rate</div><div class="metric-val" id="win-rate">–</div></div>
    <div class="metric"><div class="metric-label">Closed Trades</div><div class="metric-val" id="closed-trades">–</div></div>
    <div class="metric"><div class="metric-label">Open Now</div><div class="metric-val blue" id="open-count">–</div></div>
    <div class="metric"><div class="metric-label">Best Trade</div><div class="metric-val green" id="best-trade">–</div></div>
  </div>

  <!-- Open positions -->
  <div class="section">
    <div class="section-header">Open Positions <span id="open-badge" class="badge badge-open">0</span></div>
    <div id="open-positions">
      <div class="no-data">No open positions</div>
    </div>
  </div>

  <!-- Trade log -->
  <div class="section">
    <div class="section-header">Trade Log <span style="color:var(--muted);font-weight:400;font-size:11px;">last 50</span></div>
    <div style="overflow-x:auto">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Opened</th><th>Closed</th><th>Status</th>
          <th>ETH Entry</th><th>Call Strike</th><th>Put Strike</th>
          <th>Premium $</th><th>Opt P&L</th><th>Hedge P&L</th><th>Net P&L</th><th>P&L %</th><th>DVOL</th>
        </tr>
      </thead>
      <tbody id="trade-tbody">
        <tr><td colspan="13" class="no-data">Loading...</td></tr>
      </tbody>
    </table>
    </div>
  </div>

  <!-- Hedge log for open trades -->
  <div class="two-col">
    <div class="section">
      <div class="section-header">Live Leg Prices</div>
      <div class="hedge-log" id="hedge-log-panel">
        <div class="no-data" style="padding:20px">No hedges yet</div>
      </div>
    </div>
    <div class="section">
      <div class="section-header">Market Conditions</div>
      <table id="conditions-table">
        <tbody>
          <tr><td style="color:var(--muted)">ETH/USD</td><td id="c-eth">–</td></tr>
          <tr><td style="color:var(--muted)">DVOL</td><td id="c-dvol">–</td></tr>
          <tr><td style="color:var(--muted)">DVOL range</td><td>{{ dvol_min }}–{{ dvol_max }}</td></tr>
          <tr><td style="color:var(--muted)">Entry window</td><td>{{ entry_start }}:00–{{ entry_end }}:00 UTC</td></tr>
          <tr><td style="color:var(--muted)">Perp spread</td><td id="c-spread">–</td></tr>
          <tr><td style="color:var(--muted)">Short delta</td><td>{{ target_delta }}</td></tr>
          <tr><td style="color:var(--muted)">Structure</td><td>Short strangle</td></tr>
          <tr><td style="color:var(--muted)">Exit</td><td>SL or expiry only</td></tr>
          <tr><td style="color:var(--muted)">SL level</td><td>{{ sl_mult }}× premium</td></tr>
        </tbody>
      </table>
    </div>
  </div>

</div>

<script>
const fmt = (v, digits=2) => v == null ? '–' : Number(v).toFixed(digits);
const fmtUSD = v => v == null ? '–' : '$' + Number(v).toFixed(2);
const fmtPct = v => v == null ? '–' : (Number(v)*100).toFixed(1) + '%';
const color  = v => v >= 0 ? 'green' : 'red';

const statusBadge = s => {
  const m = {OPEN:'badge-open', CLOSED_TP:'badge-tp', CLOSED_SL:'badge-sl', CLOSED_EXPIRY:'badge-expiry'};
  const l = {OPEN:'OPEN', CLOSED_TP:'TP ✓', CLOSED_SL:'SL ✗', CLOSED_EXPIRY:'EXPIRY'};
  return `<span class="badge ${m[s]||''}">${l[s]||s}</span>`;
};

const tsToTime = ts => ts ? new Date(ts*1000).toISOString().slice(11,16)+' UTC' : '–';
const tsToDate = ts => ts ? new Date(ts*1000).toISOString().slice(0,10) : '–';
const n = (notes, key) => { try { return Object.fromEntries((notes||'').split('|').map(x=>x.split('=')))[key]||'?' } catch(e){return '?'} };

async function refresh() {
  try {
    const r = await fetch('/api/state');
    const d = await r.json();

    // KPIs
    document.getElementById('eth-price').textContent = d.market.eth_price ? '$'+Number(d.market.eth_price).toLocaleString('en',{maximumFractionDigits:0}) : '–';
    const dv = d.market.dvol;
    const dvEl = document.getElementById('dvol');
    dvEl.textContent = dv ? dv.toFixed(1) : '–';
    dvEl.className = 'metric-val ' + (dv >= 45 && dv <= 80 ? 'green' : 'amber');

    const pnl = d.stats.total_pnl || 0;
    const pnlEl = document.getElementById('total-pnl');
    pnlEl.textContent = fmtUSD(pnl);
    pnlEl.className = 'metric-val ' + color(pnl);

    const wr = d.stats.closed > 0 ? (d.stats.winners / d.stats.closed) : null;
    document.getElementById('win-rate').textContent = wr != null ? fmtPct(wr) : '–';
    document.getElementById('closed-trades').textContent = d.stats.closed || 0;
    document.getElementById('open-count').textContent = d.open_trades.length;
    document.getElementById('best-trade').textContent = fmtUSD(d.stats.best_trade);

    // market conditions sidebar
    document.getElementById('c-eth').textContent = d.market.eth_price ? '$'+Number(d.market.eth_price).toLocaleString('en',{maximumFractionDigits:2}) : '–';
    document.getElementById('c-dvol').textContent = dv ? dv.toFixed(1) : '–';
    document.getElementById('c-spread').textContent = d.market.bid_ask_spread_perp ? '$'+Number(d.market.bid_ask_spread_perp).toFixed(3) : '–';

    // open positions
    const openEl = document.getElementById('open-positions');
    document.getElementById('open-badge').textContent = d.open_trades.length;
        // render leg prices panel
        const legsEl = document.getElementById('leg-prices');
        if (legsEl && d.open_trades.length > 0) {
          const t = d.open_trades[0];
          const legs = t.legs || [];
          legsEl.innerHTML = legs.length ? `<table style="width:100%;font-size:12px">
            <thead><tr>
              <th style="text-align:left">Leg</th>
              <th>Entry $</th><th>Bid $</th><th>Ask $</th><th>Mid $</th><th>P&L $</th>
            </tr></thead><tbody>
            ${legs.map(l => {
              const pnl = (l.entry_usd - l.mid_usd) * (t.contracts || 2);
              return `<tr>
                <td>${l.name}</td>
                <td class="mono">${fmtUSD(l.entry_usd)}</td>
                <td class="mono">${fmtUSD(l.bid_usd)}</td>
                <td class="mono">${fmtUSD(l.ask_usd)}</td>
                <td class="mono">${fmtUSD(l.mid_usd)}</td>
                <td class="${color(pnl)}">${fmtUSD(pnl)}</td>
              </tr>`;
            }).join('')}
            </tbody></table>` : '<div class="no-data">No leg data yet</div>';
        }
    if (d.open_trades.length === 0) {
      openEl.innerHTML = '<div class="no-data">No open positions — scouting...</div>';
    } else {
      openEl.innerHTML = `<div style="overflow-x:auto"><table>
        <thead><tr>
          <th>#</th><th>Opened</th><th>ETH Entry</th><th>Short put</th><th>Short call</th>
          <th>Premium $</th><th>Unrealised P&L</th><th>SL Threshold</th><th>Max Loss</th><th>DVOL</th>
        </tr></thead><tbody>
        ${d.open_trades.map(t => `<tr>
          <td class="mono">#${t.id}</td>
          <td>${tsToTime(t.open_time)}</td>
          <td class="mono">$${Number(t.eth_price_entry).toLocaleString()}</td>
          <td class="mono tag">${t.long_put_strike}P/${t.put_strike}P</td>
          <td class="mono tag">${t.call_strike}C/${t.long_call_strike}C</td>
          <td>${fmtUSD(t.total_premium_collected)}</td>
          <td class="${color(t._unrealised||0)}">${fmtUSD(t._unrealised)}</td>
          <td class="red">${fmtUSD(t.stop_loss_threshold)}</td>
          <td class="red">${fmtUSD(t.stop_loss_threshold * 1.0)}</td>
          <td>${t.dvol_entry ? t.dvol_entry.toFixed(1) : '–'}</td>
        </tr>`).join('')}
        </tbody></table></div>`;
    }

    // trade log
    const tbody = document.getElementById('trade-tbody');
    if (d.all_trades.length === 0) {
      tbody.innerHTML = '<tr><td colspan="13" class="no-data">No trades yet</td></tr>';
    } else {
      tbody.innerHTML = d.all_trades.slice(0,50).map(t => `<tr>
        <td class="mono">#${t.id}</td>
        <td class="mono">${tsToDate(t.open_time)} ${tsToTime(t.open_time)}</td>
        <td class="mono">${t.close_time ? tsToTime(t.close_time) : '–'}</td>
        <td>${statusBadge(t.status)}</td>
        <td class="mono">$${Number(t.eth_price_entry||0).toLocaleString()}</td>
        <td class="mono tag">${t.call_strike}</td>
        <td class="mono tag">${t.put_strike}</td>
        <td>${fmtUSD(t.total_premium_collected)}</td>
        <td class="${color(t.option_pnl_usd||0)}">${fmtUSD(t.option_pnl_usd)}</td>
        <td class="${color(t.hedge_pnl_usd||0)}">${fmtUSD(t.hedge_pnl_usd)}</td>
        <td class="${color(t.total_pnl_usd||0)}" style="font-weight:600">${fmtUSD(t.total_pnl_usd)}</td>
        <td class="${color(t.pnl_pct||0)}">${t.pnl_pct != null ? fmtPct(t.pnl_pct) : '–'}</td>
        <td>${t.dvol_entry ? t.dvol_entry.toFixed(1) : '–'}</td>
      </tr>`).join('');
    }

    // hedge log (show for first open trade)
    const hedgePanel = document.getElementById('hedge-log-panel');
    if (d.hedge_events && d.hedge_events.length > 0) {
      hedgePanel.innerHTML = d.hedge_events.slice(-30).reverse().map(h => `
        <div class="hedge-item">
          <span style="color:${h.direction==='BUY'?'var(--green)':'var(--red)'}">${h.direction}</span>
          ${Math.abs(h.qty_eth).toFixed(4)} ETH @ $${Number(h.eth_price).toFixed(2)}
          <span style="color:var(--muted)"> | slip -$${Number(h.slippage_usd).toFixed(3)}</span>
          <span style="float:right;color:var(--muted)">${tsToTime(h.ts)}</span>
        </div>`).join('');
    } else {
      // render live leg prices
        const legsData = d.open_trades[0]?.legs || [];
        hedgePanel.innerHTML = legsData.length ? `<table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr style="border-bottom:1px solid var(--muted)">
            <th style="text-align:left;padding:6px">Leg</th>
            <th style="padding:6px">Entry $</th>
            <th style="padding:6px">Bid $</th>
            <th style="padding:6px">Ask $</th>
            <th style="padding:6px">Mid $</th>
            <th style="padding:6px">P&L $</th>
          </tr></thead><tbody>
          ${legsData.map(l => {
            const pnl = l.entry_usd - l.mid_usd;
            return `<tr style="border-bottom:1px solid var(--border)">
              <td style="padding:6px">${l.name}</td>
              <td class="mono" style="padding:6px;text-align:right">${fmtUSD(l.entry_usd)}</td>
              <td class="mono" style="padding:6px;text-align:right">${fmtUSD(l.bid_usd)}</td>
              <td class="mono" style="padding:6px;text-align:right">${fmtUSD(l.ask_usd)}</td>
              <td class="mono" style="padding:6px;text-align:right">${fmtUSD(l.mid_usd)}</td>
              <td class="mono ${color(pnl)}" style="padding:6px;text-align:right;font-weight:600">${fmtUSD(pnl)}</td>
            </tr>`;
          }).join('')}
          </tbody></table>` :
          '<div class="no-data" style="padding:20px">No open position</div>';
    }

    document.getElementById('last-update').textContent = 'Updated ' + new Date().toISOString().slice(11,19) + ' UTC';
  } catch(e) {
    console.error('refresh error', e);
  }
}

refresh();
setInterval(refresh, {{ refresh_sec }} * 1000);
</script>
</body>
</html>"""

# ── API endpoints ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    from config import (DVOL_MIN, DVOL_MAX, ENTRY_HOUR_UTC, ENTRY_HOUR_UTC_END,
                        SHORT_DELTA, TAKE_PROFIT_PCT, STOP_LOSS_MULT)
    return render_template_string(DASHBOARD_HTML,
        capital=PAPER_CAPITAL_USD,
        dvol_min=DVOL_MIN, dvol_max=DVOL_MAX,
        entry_start=ENTRY_HOUR_UTC, entry_end=ENTRY_HOUR_UTC_END,
        target_delta=SHORT_DELTA,
        tp_pct=int(TAKE_PROFIT_PCT * 100),
        sl_mult=STOP_LOSS_MULT,
        refresh_sec=DASHBOARD_REFRESH_SEC,
    )

@app.route("/api/state")
def api_state():
    snap        = db.get_latest_snapshot()
    all_trades  = db.get_all_trades()
    open_trades = db.get_open_trades()
    stats       = db.get_summary_stats()

    # inject unrealised PnL from live engine state
    if _strategy_engine:
        pos_state = _strategy_engine.get_open_positions_state()
        for t in open_trades:
            ps = pos_state.get(t["id"])
            # calculate unrealised P&L using Black-Scholes
            if ps:
                from math import log as ml, sqrt, exp
                from statistics import NormalDist
                eth = snap.get("eth_price", 0)
                dvol = snap.get("dvol", 60)
                sigma = dvol / 100.0
                expiry_ts = ps.get("expiry_ts_ms", 0)
                import time as _time
                T = max((expiry_ts - _time.time()*1000)/1000/(365.25*24*3600), 0)
                nd = NormalDist()
                def bsp(S, K, T, sig, ot):
                    if T <= 0: return max(S-K,0) if ot=="call" else max(K-S,0)
                    d1 = (ml(S/K) + 0.5*sig**2*T) / (sig*sqrt(T))
                    d2 = d1 - sig*sqrt(T)
                    return (S*nd.cdf(d1) - K*nd.cdf(d2)) if ot=="call" else (K*nd.cdf(-d2) - S*nd.cdf(-d1))
                c = ps.get("contracts", 1)
                sc_now = bsp(eth, ps.get("short_call_strike", t["call_strike"]), T, sigma, "call")
                sp_now = bsp(eth, ps.get("short_put_strike",  t["put_strike"]),  T, sigma, "put")
                sc_e = t.get("call_premium", 0) * eth
                sp_e = t.get("put_premium",  0) * eth
                # P&L = premium received - current cost to close (BS mark)
                t["_unrealised"] = ((sc_e - sc_now) + (sp_e - sp_now)) * c
            else:
                t["_unrealised"] = None
            # add live leg prices if available
            if ps:
                eth_p = snap.get("eth_price", 0)
                c = ps.get("contracts", 1)
                t["legs"] = [
                    {
                        "name": "Short call",
                        "entry_usd": t.get("call_premium", 0) * eth_p * c,
                        "bid_usd": ps.get("live_sc", {}).get("bid", 0) * eth_p * c,
                        "ask_usd": ps.get("live_sc", {}).get("ask", 0) * eth_p * c,
                        "mid_usd": ps.get("live_sc", {}).get("mid", 0) * eth_p * c,
                    },
                    {
                        "name": "Short put",
                        "entry_usd": t.get("put_premium", 0) * eth_p * c,
                        "bid_usd": ps.get("live_sp", {}).get("bid", 0) * eth_p * c,
                        "ask_usd": ps.get("live_sp", {}).get("ask", 0) * eth_p * c,
                        "mid_usd": ps.get("live_sp", {}).get("mid", 0) * eth_p * c,
                    }
                ]

    # hedge events for first open trade
    hedge_events = []
    if open_trades:
        hedge_events = db.get_hedge_events(open_trades[0]["id"])

    return jsonify({
        "market":       snap,
        "all_trades":   all_trades,
        "open_trades":  open_trades,
        "stats":        stats,
        "hedge_events": hedge_events,
    })

@app.route("/api/trades")
def api_trades():
    return jsonify(db.get_all_trades())


@app.route("/health")
def health():
    return "ok", 200

def run_dashboard():
    app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=False, use_reloader=False)
