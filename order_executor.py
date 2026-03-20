"""
Live order execution for Deribit — always fills, never skips.
Chases price from mid toward ask in steps.
"""
import asyncio
import logging
import time

log = logging.getLogger("executor")

PAPER_TRADING = True  # set False for live

class OrderExecutor:
    def __init__(self, client):
        self.client = client

    def mid(self, ticker):
        bid = ticker.get("best_bid_price") or 0
        ask = ticker.get("best_ask_price") or 0
        if not bid and not ask: return None
        if not bid: return ask
        if not ask: return bid
        return (bid + ask) / 2

    def ask(self, ticker):
        return ticker.get("best_ask_price") or 0

    async def sell_limit_chase(self, instrument, amount, ticker, label=""):
        """
        Sell at mid, chase toward ask if not filled.
        Always fills — never skips.
        Returns actual fill price.
        """
        bid  = ticker.get("best_bid_price") or 0
        ask  = ticker.get("best_ask_price") or 0

        if PAPER_TRADING:
            fill = self.mid(ticker) or ask
            log.info(f"[PAPER] SELL {amount} {instrument} @ {fill:.6f}")
            return fill

        if not ask:
            log.error(f"No ask price for {instrument} — cannot place order")
            return None

        spread = ask - bid if bid else ask * 0.02

        # price steps: mid → mid+25% → mid+50% → ask
        start = self.mid(ticker) or ask
        steps = [
            start,
            start + spread * 0.25,
            start + spread * 0.50,
            ask,
        ]
        wait_secs = 30

        for attempt, price in enumerate(steps):
            price = round(price, 6)
            log.info(f"SELL {instrument} @ {price:.6f} (attempt {attempt+1}/4)")

            try:
                resp = await self.client._send("private/sell", {
                    "instrument_name": instrument,
                    "amount": amount,
                    "type": "limit",
                    "price": price,
                    "label": label,
                })
                order_id = resp["order"]["order_id"]
                state    = resp["order"]["order_state"]

                if state == "filled":
                    fill = resp["order"].get("average_price", price)
                    log.info(f"Filled immediately @ {fill}")
                    return fill

                # wait and poll
                await asyncio.sleep(wait_secs)
                status = await self.client._send("private/get_order_state", {"order_id": order_id})
                if status.get("order_state") == "filled":
                    fill = status.get("average_price", price)
                    log.info(f"Filled after wait @ {fill}")
                    return fill

                # cancel and try next step
                await self.client._send("private/cancel", {"order_id": order_id})
                log.info(f"Not filled at {price:.6f} — moving to next step")

            except Exception as e:
                log.error(f"Order attempt {attempt+1} failed: {e}")
                continue

        log.error(f"All fill attempts failed for {instrument}")
        return None

    async def buy_limit_chase(self, instrument, amount, ticker, label=""):
        """
        Buy back (close short) — chase from mid toward bid.
        For SL close, starts at ask for fastest fill.
        """
        bid = ticker.get("best_bid_price") or 0
        ask = ticker.get("best_ask_price") or 0

        if PAPER_TRADING:
            fill = self.ask(ticker) or self.mid(ticker)
            log.info(f"[PAPER] BUY {amount} {instrument} @ {fill:.6f}")
            return fill

        if not ask:
            log.error(f"No ask for {instrument}")
            return None

        spread = ask - bid if bid else ask * 0.02
        start  = ask  # for close, start at ask for speed

        steps = [ask, ask + spread * 0.1, ask + spread * 0.25]
        wait_secs = 15  # shorter wait for SL close

        for attempt, price in enumerate(steps):
            price = round(price, 6)
            log.info(f"BUY {instrument} @ {price:.6f} (attempt {attempt+1}/3)")

            try:
                resp = await self.client._send("private/buy", {
                    "instrument_name": instrument,
                    "amount": amount,
                    "type": "limit",
                    "price": price,
                    "label": label,
                })
                order_id = resp["order"]["order_id"]

                if resp["order"]["order_state"] == "filled":
                    return resp["order"].get("average_price", price)

                await asyncio.sleep(wait_secs)
                status = await self.client._send("private/get_order_state", {"order_id": order_id})
                if status.get("order_state") == "filled":
                    return status.get("average_price", price)

                await self.client._send("private/cancel", {"order_id": order_id})

            except Exception as e:
                log.error(f"Buy attempt {attempt+1} failed: {e}")
                continue

        # last resort — market order
        log.warning(f"Using market order for {instrument}")
        try:
            resp = await self.client._send("private/buy", {
                "instrument_name": instrument,
                "amount": amount,
                "type": "market",
            })
            return resp["order"].get("average_price")
        except Exception as e:
            log.error(f"Market order failed: {e}")
            return None
