"""
Smart order executor for Deribit options.
Spread-aware pricing, atomic strangle entry, tick-size aware.
"""
import asyncio
import logging
import math

log = logging.getLogger("executor")

PAPER_TRADING      = False
TICK_SIZE          = 0.0001
MAX_ATTEMPTS       = 5
HOLD_MINUTES       = 20
WAIT_MINUTES       = 10
CHECK_INTERVAL     = 120
BID_DROP_THRESHOLD = 0.80
TIGHT_SPREAD       = 0.30
START_BELOW        = 0.90
MIN_BID_BUFFER     = 1.10

def round_tick(price):
    return math.floor(price / TICK_SIZE) * TICK_SIZE

def spread_pct(bid, ask):
    mid = (bid + ask) / 2
    return (ask - bid) / mid if mid > 0 else 999

class OrderExecutor:
    def __init__(self, client):
        self.client = client

    async def sell_strangle(self, sp_inst, sc_inst, amount, sp_ticker, sc_ticker):
        log.info(f"Entering strangle: {sp_inst} + {sc_inst} x {amount}")
        sp_fill = await self._sell_leg(sp_inst, amount, sp_ticker)
        if sp_fill is None:
            log.error("Put leg failed — skipping day")
            return None, None
        log.info(f"Put filled @ {sp_fill:.4f}")
        sc_fill = await self._sell_leg(sc_inst, amount, sc_ticker)
        if sc_fill is None:
            log.warning("Call leg failed — closing put to stay flat")
            await self._close_leg(sp_inst, amount, sp_ticker)
            return None, None
        log.info(f"Call filled @ {sc_fill:.4f}")
        return sp_fill, sc_fill

    async def _sell_leg(self, instrument, amount, ticker):
        bid = ticker.get("best_bid_price") or 0
        ask = ticker.get("best_ask_price") or 0
        if not bid or not ask:
            log.warning(f"{instrument}: no bid/ask")
            return None
        if PAPER_TRADING:
            fill = round_tick((bid + ask) / 2)
            log.info(f"[PAPER] SELL {amount} {instrument} @ {fill:.4f}")
            return fill
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                t = await self.client.get_ticker(instrument)
                bid = t.get("best_bid_price") or 0
                ask = t.get("best_ask_price") or 0
                if not bid or not ask:
                    log.warning(f"{instrument}: no market attempt {attempt}")
                    await asyncio.sleep(WAIT_MINUTES * 60)
                    continue
            except Exception as e:
                log.error(f"Ticker refresh: {e}")
                await asyncio.sleep(WAIT_MINUTES * 60)
                continue
            initial_bid = bid
            sp = spread_pct(bid, ask)
            if sp <= TIGHT_SPREAD:
                our_price = round_tick((bid + ask) / 2)
                log.info(f"Attempt {attempt}: tight {sp:.0%} → mid {our_price:.4f}")
            else:
                our_price = round_tick(ask * START_BELOW)
                log.info(f"Attempt {attempt}: wide {sp:.0%} → {our_price:.4f}")
            min_price = round_tick(bid * MIN_BID_BUFFER)
            if our_price < min_price:
                our_price = min_price
            order_id = await self._place_sell(instrument, amount, our_price)
            if order_id is None:
                await asyncio.sleep(WAIT_MINUTES * 60)
                continue
            elapsed = 0
            while elapsed < HOLD_MINUTES * 60:
                await asyncio.sleep(CHECK_INTERVAL)
                elapsed += CHECK_INTERVAL
                state = await self._order_state(order_id)
                if state == "filled":
                    fp = await self._fill_price(order_id)
                    log.info(f"{instrument}: filled @ {fp:.4f} after {elapsed//60}min")
                    return fp
                try:
                    t = await self.client.get_ticker(instrument)
                    new_bid = t.get("best_bid_price") or 0
                    new_ask = t.get("best_ask_price") or 0
                except Exception:
                    continue
                if new_bid < initial_bid * BID_DROP_THRESHOLD:
                    log.warning(f"{instrument}: bid dropped {initial_bid:.4f}→{new_bid:.4f} — cancel")
                    await self._cancel(order_id)
                    break
                if new_ask < our_price and new_ask > new_bid * MIN_BID_BUFFER:
                    new_price = round_tick(new_ask * START_BELOW)
                    min_p = round_tick(new_bid * MIN_BID_BUFFER)
                    if new_price < min_p:
                        new_price = min_p
                    if new_price != our_price:
                        log.info(f"{instrument}: undercut → {new_price:.4f}")
                        await self._cancel(order_id)
                        our_price = new_price
                        order_id = await self._place_sell(instrument, amount, our_price)
                        if order_id is None:
                            break
            await self._cancel(order_id)
            log.info(f"{instrument}: attempt {attempt} timed out — waiting {WAIT_MINUTES}min")
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(WAIT_MINUTES * 60)
        log.error(f"{instrument}: all {MAX_ATTEMPTS} attempts failed")
        return None

    async def _close_leg(self, instrument, amount, ticker):
        if PAPER_TRADING:
            log.info(f"[PAPER] CLOSE {instrument}")
            return
        ask = ticker.get("best_ask_price") or 0
        if not ask:
            try:
                t = await self.client.get_ticker(instrument)
                ask = t.get("best_ask_price") or 0
            except Exception:
                pass
        if not ask:
            log.error(f"Cannot close {instrument} — no ask")
            return
        price = round_tick(ask)
        for attempt in range(3):
            oid = await self._place_buy(instrument, amount, price)
            if oid is None:
                continue
            await asyncio.sleep(30)
            if await self._order_state(oid) == "filled":
                log.info(f"Closed {instrument}")
                return
            await self._cancel(oid)
            price = round_tick(price * 1.05)
        try:
            await self.client._send("private/buy", {"instrument_name": instrument, "amount": amount, "type": "market"})
        except Exception as e:
            log.error(f"Market close failed: {e}")

    async def buy_limit_chase(self, instrument, amount, ticker, label=""):
        ask = ticker.get("best_ask_price") or 0
        if PAPER_TRADING:
            log.info(f"[PAPER] BUY {amount} {instrument} @ {ask:.4f}")
            return ask
        if not ask:
            return None
        price = round_tick(ask)
        for attempt in range(3):
            oid = await self._place_buy(instrument, amount, price)
            if oid is None:
                continue
            await asyncio.sleep(30)
            if await self._order_state(oid) == "filled":
                return await self._fill_price(oid)
            await self._cancel(oid)
            price = round_tick(price * 1.05)
        try:
            resp = await self.client._send("private/buy", {"instrument_name": instrument, "amount": amount, "type": "market"})
            return resp.get("order", {}).get("average_price")
        except Exception as e:
            log.error(f"Market buy failed: {e}")
            return None

    async def _place_sell(self, instrument, amount, price):
        price = round_tick(price)
        log.info(f"SELL {amount} {instrument} @ {price:.4f}")
        try:
            resp = await self.client._send("private/sell", {
                "instrument_name": instrument, "amount": amount,
                "type": "limit", "price": price, "post_only": True,
            })
            if resp["order"]["order_state"] == "filled":
                fp = resp["order"].get("average_price", price)
                return f"FILLED:{fp}"
            return resp["order"]["order_id"]
        except Exception as e:
            log.error(f"Place sell failed: {e}")
            return None

    async def _place_buy(self, instrument, amount, price):
        price = round_tick(price)
        try:
            resp = await self.client._send("private/buy", {
                "instrument_name": instrument, "amount": amount,
                "type": "limit", "price": price,
            })
            return resp["order"]["order_id"]
        except Exception as e:
            log.error(f"Place buy failed: {e}")
            return None

    async def _order_state(self, order_id):
        if isinstance(order_id, str) and order_id.startswith("FILLED:"):
            return "filled"
        try:
            resp = await self.client._send("private/get_order_state", {"order_id": order_id})
            return resp.get("order_state", "unknown")
        except Exception:
            return "unknown"

    async def _fill_price(self, order_id):
        if isinstance(order_id, str) and order_id.startswith("FILLED:"):
            return float(order_id.split(":")[1])
        try:
            resp = await self.client._send("private/get_order_state", {"order_id": order_id})
            return resp.get("average_price", 0)
        except Exception:
            return 0

    async def _cancel(self, order_id):
        if isinstance(order_id, str) and order_id.startswith("FILLED:"):
            return
        try:
            await self.client._send("private/cancel", {"order_id": order_id})
        except Exception:
            pass

    def mid(self, ticker):
        bid = ticker.get("best_bid_price") or 0
        ask = ticker.get("best_ask_price") or 0
        return (bid + ask) / 2 if bid and ask else ask or bid or 0

    def ask(self, ticker):
        return ticker.get("best_ask_price") or 0

    async def sell_strangle_with_deadline(self, sp_inst, sc_inst, amount, sp_ticker, sc_ticker, deadline=None):
        """sell_strangle but with a 120-min deadline from first fill."""
        import time
        log.info(f"Entering strangle: {sp_inst} + {sc_inst} x {amount}")
        sp_fill = await self._sell_leg(sp_inst, amount, sp_ticker)
        if sp_fill is None:
            log.error("Put leg failed — skipping day")
            return None, None
        log.info(f"Put filled @ {sp_fill:.4f}")
        # set deadline from first fill
        fill_deadline = time.time() + 120 * 60
        sc_fill = await self._sell_leg_with_deadline(sc_inst, amount, sc_ticker, fill_deadline)
        if sc_fill is None:
            log.warning("Call leg failed — closing put to stay flat")
            await self._close_leg(sp_inst, amount, sp_ticker)
            return None, None
        log.info(f"Call filled @ {sc_fill:.4f}")
        return sp_fill, sc_fill

    async def _sell_leg_with_deadline(self, instrument, amount, ticker, deadline):
        """Like _sell_leg but stops if deadline exceeded."""
        import time
        bid = ticker.get("best_bid_price") or 0
        ask = ticker.get("best_ask_price") or 0
        if not bid or not ask:
            return None
        if PAPER_TRADING:
            fill = round_tick((bid + ask) / 2)
            log.info(f"[PAPER] SELL {amount} {instrument} @ {fill:.4f}")
            return fill
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if time.time() > deadline:
                log.warning(f"{instrument}: 120-min deadline exceeded — giving up")
                return None
            result = await self._single_attempt(instrument, amount, ticker, attempt)
            if result is not None:
                return result
            if attempt < MAX_ATTEMPTS:
                if time.time() + WAIT_MINUTES * 60 > deadline:
                    log.warning(f"{instrument}: not enough time for retry — closing put")
                    return None
                await asyncio.sleep(WAIT_MINUTES * 60)
        return None

    async def _single_attempt(self, instrument, amount, ticker, attempt_num):
        """One 20-min attempt to fill a sell order."""
        try:
            t = await self.client.get_ticker(instrument)
            bid = t.get("best_bid_price") or 0
            ask = t.get("best_ask_price") or 0
            if not bid or not ask:
                return None
        except Exception as e:
            log.error(f"Ticker: {e}")
            return None
        initial_bid = bid
        sp = spread_pct(bid, ask)
        our_price = round_tick((bid+ask)/2) if sp <= TIGHT_SPREAD else round_tick(ask * START_BELOW)
        our_price = max(our_price, round_tick(bid * MIN_BID_BUFFER))
        log.info(f"Attempt {attempt_num}: {instrument} spread={sp:.0%} price={our_price:.4f}")
        order_id = await self._place_sell(instrument, amount, our_price)
        if order_id is None:
            return None
        elapsed = 0
        while elapsed < HOLD_MINUTES * 60:
            await asyncio.sleep(CHECK_INTERVAL)
            elapsed += CHECK_INTERVAL
            state = await self._order_state(order_id)
            if state == "filled":
                fp = await self._fill_price(order_id)
                log.info(f"{instrument}: filled @ {fp:.4f}")
                return fp
            try:
                t = await self.client.get_ticker(instrument)
                new_bid = t.get("best_bid_price") or 0
                new_ask = t.get("best_ask_price") or 0
            except Exception:
                continue
            if new_bid < initial_bid * BID_DROP_THRESHOLD:
                log.warning(f"{instrument}: bid dropped — cancel")
                await self._cancel(order_id)
                return None
            if new_ask < our_price and new_ask > new_bid * MIN_BID_BUFFER:
                new_price = max(round_tick(new_ask * START_BELOW), round_tick(new_bid * MIN_BID_BUFFER))
                if new_price != our_price:
                    log.info(f"{instrument}: undercut → {new_price:.4f}")
                    await self._cancel(order_id)
                    our_price = new_price
                    order_id = await self._place_sell(instrument, amount, our_price)
                    if order_id is None:
                        return None
        await self._cancel(order_id)
        return None
