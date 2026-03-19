"""
Live order execution module for Deribit.
Handles limit order placement, polling, and cancellation.
Paper trading: simulates fills without placing real orders.
"""
import asyncio
import logging
import time

log = logging.getLogger("order_executor")

PAPER_TRADING = True  # set False for live


class OrderExecutor:
    def __init__(self, client):
        self.client = client

    async def sell_limit(self, instrument, amount, price, label=""):
        """Sell (short) at limit price. Returns fill_price or None."""
        if PAPER_TRADING:
            log.info(f"[PAPER] SELL {amount} {instrument} @ {price:.6f}")
            return price

        try:
            resp = await self.client._send("private/sell", {
                "instrument_name": instrument,
                "amount": amount,
                "type": "limit",
                "price": price,
                "post_only": True,
                "label": label,
            })
            order_id = resp["order"]["order_id"]
            log.info(f"Placed SELL limit #{order_id} {amount} {instrument} @ {price:.6f}")
            return await self._wait_fill(order_id, instrument, price)
        except Exception as e:
            log.error(f"sell_limit failed {instrument}: {e}")
            return None

    async def buy_limit(self, instrument, amount, price, label="", aggressive=False):
        """Buy (close short) at limit price. aggressive=True uses ask for SL close."""
        if PAPER_TRADING:
            log.info(f"[PAPER] BUY {amount} {instrument} @ {price:.6f}")
            return price

        try:
            resp = await self.client._send("private/buy", {
                "instrument_name": instrument,
                "amount": amount,
                "type": "limit",
                "price": price,
                "label": label,
            })
            order_id = resp["order"]["order_id"]
            log.info(f"Placed BUY limit #{order_id} {amount} {instrument} @ {price:.6f}")
            fill = await self._wait_fill(order_id, instrument, price, retries=3 if aggressive else 6)
            if fill is None and aggressive:
                # escalate to market order for SL
                log.warning(f"Limit unfilled — escalating to market for {instrument}")
                fill = await self._market_buy(order_id, instrument, amount)
            return fill
        except Exception as e:
            log.error(f"buy_limit failed {instrument}: {e}")
            return None

    async def _wait_fill(self, order_id, instrument, price, retries=6, interval=10):
        """Poll order status until filled or retries exhausted."""
        for attempt in range(retries):
            await asyncio.sleep(interval)
            try:
                resp = await self.client._send("private/get_order_state", {
                    "order_id": order_id
                })
                state = resp.get("order_state")
                if state == "filled":
                    fill_price = resp.get("average_price", price)
                    log.info(f"Order #{order_id} FILLED @ {fill_price}")
                    return fill_price
                elif state in ("cancelled", "rejected"):
                    log.warning(f"Order #{order_id} {state}")
                    return None
                else:
                    log.info(f"Order #{order_id} {state} (attempt {attempt+1}/{retries})")
            except Exception as e:
                log.error(f"poll order {order_id}: {e}")

        # cancel unfilled order
        try:
            await self.client._send("private/cancel", {"order_id": order_id})
            log.warning(f"Order #{order_id} cancelled after {retries} attempts")
        except Exception as e:
            log.error(f"cancel order {order_id}: {e}")
        return None

    async def _market_buy(self, cancel_order_id, instrument, amount):
        """Last resort market buy for SL close."""
        try:
            resp = await self.client._send("private/buy", {
                "instrument_name": instrument,
                "amount": amount,
                "type": "market",
            })
            fill = resp.get("order", {}).get("average_price")
            log.warning(f"Market BUY {instrument} filled @ {fill}")
            return fill
        except Exception as e:
            log.error(f"market_buy failed: {e}")
            return None

    def mid_price(self, ticker):
        """Calculate mid price from ticker."""
        bid = ticker.get("best_bid_price", 0) or 0
        ask = ticker.get("best_ask_price", 0) or 0
        if bid <= 0 or ask <= 0:
            return None
        return round((bid + ask) / 2, 6)

    def ask_price(self, ticker):
        """Get ask price for aggressive close."""
        return ticker.get("best_ask_price", 0) or 0
