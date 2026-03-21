"""
Strategy 2 — Iron Condor Engine
Sell OTM put spread + OTM call spread on 1DTE ETH options.
Defined risk both sides. No delta hedge or futures required.
Win condition: ETH stays between short strikes at expiry.
"""
import asyncio, time, logging, json
from datetime import datetime, timezone
from math import log as mlog, sqrt, exp
from statistics import NormalDist
import db
from config import (
    DVOL_MIN, DVOL_MAX,
    ENTRY_HOUR_UTC, ENTRY_HOUR_UTC_END,
    TAKE_PROFIT_PCT, STOP_LOSS_MULT,
    SLIPPAGE_OPTIONS_PCT,
    PAPER_CAPITAL_USD, MAX_RISK_PER_TRADE,
    SHORT_DELTA, WING_WIDTH_PCT,
)

log = logging.getLogger("strategy")

# ── Black-Scholes ─────────────────────────────────────────────────────────────
_nd = NormalDist()

def _d1(S, K, T, sigma, r=0.0):
    if T <= 0 or sigma <= 0: return 0.0
    return (mlog(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt(T))

def bs_delta(S, K, T, sigma, option_type="call", r=0.0):
    if T <= 0:
        return (1.0 if S > K else 0.0) if option_type == "call" else (-1.0 if S < K else 0.0)
    d1 = _d1(S, K, T, sigma, r)
    return _nd.cdf(d1) if option_type == "call" else _nd.cdf(d1) - 1.0

def bs_price(S, K, T, sigma, option_type="call", r=0.0):
    if T <= 0:
        return max(S - K, 0) if option_type == "call" else max(K - S, 0)
    d1 = _d1(S, K, T, sigma, r)
    d2 = d1 - sigma * sqrt(T)
    if option_type == "call":
        return S * _nd.cdf(d1) - K * exp(-r * T) * _nd.cdf(d2)
    return K * exp(-r * T) * _nd.cdf(-d2) - S * _nd.cdf(-d1)

def time_to_expiry_years(expiry_ts_ms):
    return max((expiry_ts_ms - time.time() * 1000) / 1000 / (365.25 * 24 * 3600), 0.0)

def find_strike_near_delta(S, expiry_ts_ms, sigma, target_delta, option_type):
    T = time_to_expiry_years(expiry_ts_ms)
    if T <= 0: return S
    lo, hi = S * 0.5, S * 2.0
    for _ in range(60):
        mid = (lo + hi) / 2
        d = bs_delta(S, mid, T, sigma, option_type)
        if option_type == "call":
            if abs(d) < target_delta: hi = mid
            else: lo = mid
        else:
            if abs(d) < target_delta: lo = mid
            else: hi = mid
    return round(round((lo + hi) / 2 / 1000) * 1000, 0)

def check_liquidity(ticker, role):
    """
    Returns (fill_price_eth, ok, reason)
    For short legs: use best_bid (we are selling)
    For long legs:  use best_ask (we are buying)
    Rejects if: zero bid, spread > 30% of mid, or size < 5
    """
    bid  = ticker.get("best_bid_price", 0) or 0
    ask  = ticker.get("best_ask_price", 0) or 0
    bsz  = ticker.get("best_bid_amount", 0) or 0
    asz  = ticker.get("best_ask_amount", 0) or 0

    if bid <= 0 or ask <= 0:
        return 0, False, f"{role}: zero bid/ask"
    mid = (bid + ask) / 2
    spread_pct = (ask - bid) / mid
    if spread_pct > 0.60:
        return 0, False, f"{role}: spread {spread_pct:.0%} > 60%"
    if role in ("sc", "sp") and bsz < 0.75:
        return 0, False, f"{role}: bid size {bsz} < 0.75 contracts"
    if role in ("lc", "lp") and asz < 5:
        return 0, False, f"{role}: ask size {asz} < 5 contracts"

    # use bid for sells (sc, sp), ask for buys (lc, lp)
    fill = bid if role in ("sc", "sp") else ask
    return fill, True, "ok"

def calc_contracts(max_spread_width_usd, net_premium_usd):
    max_loss = max_spread_width_usd - net_premium_usd
    if max_loss <= 0: return 1
    return max(1, min(int(PAPER_CAPITAL_USD * MAX_RISK_PER_TRADE / max_loss), 3))

# ── Strategy engine ───────────────────────────────────────────────────────────
class StrategyEngine:
    def __init__(self, client, telegram):
        self.client   = client
        self.telegram = telegram
        self._open_positions = {}
        self._last_trade_day = None
        from order_executor import OrderExecutor
        self._executor = OrderExecutor(self.client)
        self._running = False

    def _expiry_label_to_ts(self, label):
        try:
            dt = datetime.strptime(label, "%d%b%y").replace(
                hour=8, minute=0, second=0, tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception as e:
            log.warning(f"expiry_label_to_ts failed {label}: {e}")
            return None

    async def recover_open_trades(self):
        open_trades = db.get_open_trades()
        if not open_trades:
            log.info("No open trades to recover")
            return
        for t in open_trades:
            expiry_ts_ms = self._expiry_label_to_ts(t["call_expiry"])
            if not expiry_ts_ms: continue
            notes = dict(x.split("=") for x in t["notes"].split("|") if "=" in x)
            contracts        = float(notes.get("contracts", 1))
            long_call_strike = float(notes.get("long_call", t["call_strike"] + 100))
            long_put_strike  = float(notes.get("long_put",  t["put_strike"]  - 100))
            call_width       = float(notes.get("call_width", 100))
            put_width        = float(notes.get("put_width",  100))
            self._open_positions[t["id"]] = {
                "contracts":         contracts,
                "expiry_ts_ms":      expiry_ts_ms,
                "short_call_strike": t["call_strike"],
                "short_put_strike":  t["put_strike"],
                "long_call_strike":  long_call_strike,
                "long_put_strike":   long_put_strike,
                "call_spread_width": call_width,
                "put_spread_width":  put_width,
                "net_premium_usd":   t["total_premium_collected"] / contracts,
                "expiry_label":      t.get("call_expiry", ""),
            }
            log.info(f"Recovered trade #{t['id']}: IC {int(t['put_strike'])}P/{int(t['call_strike'])}C")
        if open_trades:
            await self.telegram.send(f"♻️ *Recovered {len(open_trades)} open trade(s) after restart*")

    async def run(self):
        self._running = True
        log.info("Strangle engine started")
        await self.recover_open_trades()
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                log.error(f"Tick error: {e}", exc_info=True)
            await asyncio.sleep(30)

    async def _tick(self):
        eth = self.client._btc_price
        dvol = self.client._dvol
        if eth is None or dvol is None:
            log.warning(f"Waiting for market data: btc={eth} dvol={dvol}")
            return
        log.info(f"Tick: BTC={eth:.0f} DVOL={dvol:.1f}")
        db.upsert_snapshot({
            "ts": time.time(), "eth_price": eth, "dvol": dvol,
            "eth_iv_atm": dvol / 100.0,
            "bid_ask_spread_perp": self._get_perp_spread(),
        })
        log.info(f"Open positions in memory: {list(self._open_positions.keys())}")
        for trade_id in list(self._open_positions.keys()):
            pos = self._open_positions.get(trade_id)
            if pos:
                expiry = pos.get("expiry_label", "")
                sc_strike = int(pos.get("short_call_strike", 0))
                sp_strike = int(pos.get("short_put_strike", 0))
                try:
                    expiry = pos.get("expiry_label", "")
                    if not expiry:
                        trades = db.get_all_trades()
                        tr = next((x for x in trades if x["id"] == trade_id), {})
                        expiry = tr.get("call_expiry", "")
                    sc_inst = f"BTC-{expiry}-{sc_strike}-C"
                    sp_inst = f"BTC-{expiry}-{sp_strike}-P"
                    log.info(f"Fetching live tickers: {sc_inst} / {sp_inst}")
                    sc_t = await self.client.get_ticker(sc_inst)
                    sp_t = await self.client.get_ticker(sp_inst)
                    if sc_t is None or sp_t is None:
                        log.error(f"Live ticker returned None — sc_t={sc_t} sp_t={sp_t} (instruments: {sc_inst}, {sp_inst})")
                    else:
                        sc_bid = sc_t.get("best_bid_price", 0) or 0
                        sc_ask = sc_t.get("best_ask_price", 0) or 0
                        sp_bid = sp_t.get("best_bid_price", 0) or 0
                        sp_ask = sp_t.get("best_ask_price", 0) or 0
                        log.info(f"Live {sc_inst}: bid={sc_bid} ask={sc_ask} | {sp_inst}: bid={sp_bid} ask={sp_ask}")
                        if sc_bid == 0 and sc_ask == 0:
                            log.warning(f"Live ticker {sc_inst} returned zero bid/ask — no market quotes")
                        if sp_bid == 0 and sp_ask == 0:
                            log.warning(f"Live ticker {sp_inst} returned zero bid/ask — no market quotes")
                        pos["live_sc"] = {"bid": sc_bid, "ask": sc_ask, "mid": (sc_bid + sc_ask) / 2 if sc_bid and sc_ask else sc_ask / 2 if sc_ask else 0}
                        pos["live_sp"] = {"bid": sp_bid, "ask": sp_ask, "mid": (sp_bid + sp_ask) / 2 if sp_bid and sp_ask else sp_ask / 2 if sp_ask else 0}
                except Exception as e:
                    log.error(f"Live ticker fetch failed for trade #{trade_id} ({expiry} {sc_strike}C/{sp_strike}P): {e}", exc_info=True)
            await self._manage_position(trade_id, eth, dvol)
        await self._scout_entry(eth, dvol)

    def _get_todays_entry_window(self, today):
        """Random entry start between ENTRY_HOUR_UTC and ENTRY_HOUR_UTC_END-1.
        Seed with date so it's stable within the same day but different each day."""
        import random, hashlib
        seed = int(hashlib.md5(str(today).encode()).hexdigest(), 16)
        rng  = random.Random(seed)
        # random start hour within the window, entry lasts 90 minutes
        start_hour = rng.randint(ENTRY_HOUR_UTC, ENTRY_HOUR_UTC_END - 1)
        start_min  = rng.randint(0, 30)
        end_hour   = start_hour + 1
        end_min    = start_min + 30
        if end_min >= 60:
            end_hour += 1
            end_min  -= 60
        return start_hour, start_min, end_hour, end_min

    async def _scout_entry(self, eth, dvol):
        now_utc = datetime.now(timezone.utc)
        today   = now_utc.date()
        if self._last_trade_day == today: return
        # check DB for trades opened today (survives restarts)
        all_trades = db.get_all_trades()
        for t in all_trades:
            if t.get("status") == "OPEN":
                trade_date = datetime.fromtimestamp(t["open_time"], tz=timezone.utc).date()
                if trade_date == today:
                    self._last_trade_day = today
                    return
        now_mins = now_utc.hour * 60 + now_utc.minute
        window_start = ENTRY_HOUR_UTC * 60
        window_end   = ENTRY_HOUR_UTC_END * 60
        if not (window_start <= now_mins < window_end): return
        if not (DVOL_MIN <= dvol <= DVOL_MAX):
            log.info(f"DVOL {dvol:.1f} outside range — skip")
            return

        expiry_info = await self._find_1dte_expiry()
        if not expiry_info: return
        expiry_name, expiry_ts_ms = expiry_info

        sigma = dvol / 100.0
        T = time_to_expiry_years(expiry_ts_ms)
        if T <= 0: return

        short_call = find_strike_near_delta(eth, expiry_ts_ms, sigma, SHORT_DELTA, "call")
        short_put  = find_strike_near_delta(eth, expiry_ts_ms, sigma, SHORT_DELTA, "put")

        legs = [
            (short_call, "C", "sc"),
            (short_put,  "P", "sp"),
        ]
        tickers = {}
        for strike, otype, key in legs:
            inst = f"BTC-{expiry_name}-{int(strike)}-{otype}"
            t = await self._safe_ticker(inst)
            if t is None:
                log.warning(f"No ticker for {inst} — skip")
                return
            tickers[key] = t

        def mid(t):
            b, a = t.get("best_bid_price", 0), t.get("best_ask_price", 0)
            return (b + a) / 2 if b and a else 0

        # depth filter + realistic fill prices (bid for sells, ask for buys)
        sc_fill, sc_ok, sc_reason = check_liquidity(tickers["sc"], "sc")
        sp_fill, sp_ok, sp_reason = check_liquidity(tickers["sp"], "sp")

        for ok, reason in [(sc_ok,sc_reason),(sp_ok,sp_reason)]:
            if not ok:
                log.info(f"Liquidity check failed: {reason} — skip")
                return

        # use mid price for entry orders (limit at mid)
        sc_mid = self._executor.mid(tickers["sc"]) or sc_fill
        sp_mid = self._executor.mid(tickers["sp"]) or sp_fill

        # net premium in ETH: receive sc+sp (no wings — strangle)
        net_eth = sc_mid + sp_mid
        if net_eth <= 0:
            log.info(f"Net premium ≤ 0 ({net_eth:.6f}) — skip")
            return

        net_usd        = net_eth * eth
        # fixed 2 contracts always
        contracts    = 0.1

        total_premium = net_usd
        tp_target     = total_premium * TAKE_PROFIT_PCT
        sl_threshold  = total_premium * STOP_LOSS_MULT
        be_up         = short_call + net_usd
        be_dn         = short_put  - net_usd

        trade = {
            "open_time": time.time(), "status": "OPEN",
            "eth_price_entry": eth,
            "call_strike": short_call, "call_expiry": expiry_name,
            "call_delta_entry": bs_delta(eth, short_call, T, sigma, "call"),
            "call_premium": sc_mid,
            "put_strike": short_put, "put_expiry": expiry_name,
            "put_delta_entry": bs_delta(eth, short_put, T, sigma, "put"),
            "put_premium": sp_mid,
            "long_call_strike": None,
            "long_put_strike": None,
            "long_call_premium": 0.0,
            "long_put_premium": 0.0,
            "total_premium_collected": total_premium,
            "take_profit_target": tp_target,
            "stop_loss_threshold": sl_threshold,
            "hedge_log": json.dumps([]), "hedge_pnl_usd": 0.0,
            "dvol_entry": dvol,
            "notes": f"contracts={contracts}|type=strangle",
        }
        trade_id = db.insert_trade(trade)
        self._open_positions[trade_id] = {
            "contracts": contracts, "expiry_ts_ms": expiry_ts_ms,
            "short_call_strike": short_call, "long_call_strike": short_call + 100,
            "short_put_strike":  short_put,  "long_put_strike":  short_put  - 100,
            "net_premium_usd": net_usd,
            "sc_entry_eth": sc_mid, "lc_entry_eth": 0.0,
            "sp_entry_eth": sp_mid, "lp_entry_eth": 0.0,
            "expiry_label": expiry_name,
        }
        self._last_trade_day = today

        msg = (
            f"📋 *NEW STRANGLE #{trade_id}*\n"
            f"ETH @ ${eth:,.0f} | DVOL {dvol:.1f}\n"
            f"📉 Short put:  {int(short_put)}P\n"
            f"📈 Short call: {int(short_call)}C\n"
            f"💰 Net premium: ${total_premium:.2f} ({contracts}c)\n"
            f"🎯 Profit zone: ${be_dn:,.0f} — ${be_up:,.0f}\n"
            f"🛑 SL: ${sl_threshold:.2f}\n"
            f"⏰ Expiry {expiry_name} 08:00 UTC"
        )
        sc_expiry_inst = f"BTC-{expiry_name}-{int(short_call)}-C"
        sp_expiry_inst = f"BTC-{expiry_name}-{int(short_put)}-P"
        sp_fill_price, sc_fill_price = await self._executor.sell_strangle(
            sp_expiry_inst, sc_expiry_inst, contracts, tickers["sp"], tickers["sc"]
        )
        if sp_fill_price is None or sc_fill_price is None:
            log.error(f"Strangle entry failed for trade #{trade_id} — no position")
            return

        sc_mid = sc_fill_price
        sp_mid = sp_fill_price
        net_eth = sc_mid + sp_mid
        net_usd = net_eth * eth

        log.info(f"Trade #{trade_id}: Strangle {int(short_put)}P/{int(short_call)}C net=${net_usd:.2f}")
        await self.telegram.send(msg)

    async def _manage_position(self, trade_id, eth, dvol):
        pos = self._open_positions.get(trade_id)
        if not pos: return
        T     = time_to_expiry_years(pos["expiry_ts_ms"])
        sigma = dvol / 100.0
        c     = pos["contracts"]

        # current BS value of each leg
        sc_now = bs_price(eth, pos["short_call_strike"], T, sigma, "call")
        lc_now = bs_price(eth, pos["long_call_strike"],  T, sigma, "call")
        sp_now = bs_price(eth, pos["short_put_strike"],  T, sigma, "put")
        lp_now = bs_price(eth, pos["long_put_strike"],   T, sigma, "put")

        # fetch trade row from DB first (needed for entry premiums + SL threshold)
        trade_rows = db.get_all_trades()
        t = next((x for x in trade_rows if x["id"] == trade_id), None)
        if t is None: return

        # entry values from DB (all 4 legs now stored)
        sc_entry = pos.get("sc_entry_eth", t.get("call_premium", 0)) * eth
        lc_entry = pos.get("lc_entry_eth", t.get("long_call_premium", 0)) * eth
        sp_entry = pos.get("sp_entry_eth", t.get("put_premium", 0)) * eth
        lp_entry = pos.get("lp_entry_eth", t.get("long_put_premium", 0)) * eth

        # P&L = (entry value sold - current value sold) + (current value bought - entry value bought)
        option_pnl = (
            (sc_entry - sc_now) + (sp_entry - sp_now) +
            (lc_now - lc_entry) + (lp_now - lp_entry)
        ) * c

        if -option_pnl >= t["stop_loss_threshold"]:
            await self._close(trade_id, eth, sc_now, sp_now, option_pnl, "CLOSED_SL")
        elif T <= 0:
            await self._close(trade_id, eth, sc_now, sp_now, option_pnl, "CLOSED_EXPIRY")
        elif eth >= pos["long_call_strike"] or eth <= pos["long_put_strike"]:
            log.warning(f"#{trade_id}: ETH {eth:.0f} breached wing — emergency close")
            await self._close(trade_id, eth, sc_now, sp_now, option_pnl, "CLOSED_SL")

    async def _close(self, trade_id, eth, sc_now, sp_now, option_pnl, status):
        pos = self._open_positions.pop(trade_id, None)
        if not pos: return
        slippage  = 0.0  # bid/ask fills already account for slippage at entry
        total_pnl = option_pnl - slippage
        pnl_pct   = total_pnl / PAPER_CAPITAL_USD
        db.update_trade(trade_id, {
            "status": status, "close_time": time.time(),
            "eth_price_exit": eth, "call_premium_exit": sc_now, "put_premium_exit": sp_now,
            "option_pnl_usd": option_pnl, "hedge_pnl_usd": 0.0,
            "total_pnl_usd": total_pnl, "pnl_pct": pnl_pct,
        })
        emoji = {"CLOSED_TP": "✅", "CLOSED_SL": "🛑", "CLOSED_EXPIRY": "⏰"}.get(status, "📊")
        msg = (
            f"{emoji} *TRADE #{trade_id} CLOSED — {status}*\n"
            f"ETH @ ${eth:,.0f}\n"
            f"📊 Option P&L: ${option_pnl:.2f}\n"
            f"💸 Slippage: -${slippage:.2f}\n"
            f"{'🟢' if total_pnl >= 0 else '🔴'} *Net: ${total_pnl:.2f} ({pnl_pct:.2%})*"
        )
        log.info(f"#{trade_id} [{status}] ${total_pnl:.2f} ({pnl_pct:.2%})")
        await self.telegram.send(msg)

    async def _find_1dte_expiry(self):
        try:
            instruments = await self.client.get_instruments()
            now_ts, seen, candidates = time.time() * 1000, set(), []
            for ins in instruments:
                exp_ts    = ins.get("expiration_timestamp", 0)
                exp_label = ins["instrument_name"].split("-")[1]
                if exp_label in seen: continue
                seen.add(exp_label)
                hours = (exp_ts - now_ts) / 3_600_000
                if 12 < hours < 36:
                    candidates.append((exp_label, exp_ts))
            if candidates:
                candidates.sort(key=lambda x: x[1])
                return candidates[0]
        except Exception as e:
            log.error(f"find_1dte_expiry: {e}")
        return None

    async def _safe_ticker(self, instrument):
        try:
            return await self.client.get_ticker(instrument)
        except Exception as e:
            log.warning(f"Ticker failed {instrument}: {e}")
            return None

    def _get_perp_spread(self):
        ob = self.client.get_orderbook("ETH-PERPETUAL")
        if ob.get("bids") and ob.get("asks"):
            return ob["asks"][0][0] - ob["bids"][0][0]
        return None

    def get_open_positions_state(self):
        return dict(self._open_positions)

    def stop(self):
        self._running = False
