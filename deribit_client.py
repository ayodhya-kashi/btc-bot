import asyncio, json, time, logging
import websockets
from config import DERIBIT_WS_URL, DERIBIT_CLIENT_ID, DERIBIT_CLIENT_SECRET

log = logging.getLogger("deribit_ws")

class DeribitClient:
    def __init__(self):
        self.ws              = None
        self._msg_id         = 0
        self._pending        = {}
        self._btc_price      = None
        self._dvol           = None
        self._orderbooks     = {}
        self._callbacks      = []
        self._running        = False
        self._last_price_ts  = None

    def _next_id(self):
        self._msg_id += 1
        return self._msg_id

    async def _send_and_drain(self, method, params=None):
        msg_id = self._next_id()
        payload = {"jsonrpc": "2.0", "id": msg_id,
                   "method": method, "params": params or {}}
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[msg_id] = fut
        await self.ws.send(json.dumps(payload))
        deadline = loop.time() + 10
        while not fut.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"Timeout waiting for {method}")
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
                await self._handle_message(raw)
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(f"Timeout waiting for {method}")
        return fut.result()

    async def _send(self, method, params=None):
        msg_id = self._next_id()
        payload = {"jsonrpc": "2.0", "id": msg_id,
                   "method": method, "params": params or {}}
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[msg_id] = fut
        await self.ws.send(json.dumps(payload))
        return await asyncio.wait_for(fut, timeout=10)

    async def _handle_message(self, raw):
        msg = json.loads(raw)
        if "id" in msg and msg["id"] in self._pending:
            fut = self._pending.pop(msg["id"])
            if not fut.done():
                if "error" in msg:
                    fut.set_exception(Exception(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
            return
        if msg.get("method") == "heartbeat":
            if msg.get("params", {}).get("type") == "test_request":
                await self.ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": self._next_id(),
                    "method": "public/test", "params": {}
                }))
            return
        if msg.get("method") == "subscription":
            channel = msg["params"].get("channel", "")
            data    = msg["params"].get("data", {})
            if channel == "deribit_price_index.btc_usd":
                self._btc_price     = data.get("price")
                self._last_price_ts = time.time()
            elif "volatility_index" in channel:
                self._dvol = data.get("volatility")
            elif channel.startswith("book."):
                inst = channel.split(".")[1]
                self._orderbooks[inst] = {
                    "bids": data.get("bids", []),
                    "asks": data.get("asks", []),
                    "ts":   time.time(),
                }

    async def _authenticate(self):
        await self._send_and_drain("public/auth", {
            "grant_type":    "client_credentials",
            "client_id":     DERIBIT_CLIENT_ID,
            "client_secret": DERIBIT_CLIENT_SECRET,
        })
        log.info("Authenticated with Deribit")

    async def _subscribe_base(self):
        await self._send_and_drain("public/subscribe", {"channels": [
            "deribit_price_index.btc_usd",
            "deribit_volatility_index.eth_usd",
            "book.ETH-PERPETUAL.none.20.100ms",
        ]})
        log.info("Subscribed to base channels")

    async def connect(self):
        self._running = True
        while self._running:
            try:
                async with websockets.connect(
                    DERIBIT_WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    max_size=2**23,
                ) as ws:
                    self.ws = ws
                    for fut in self._pending.values():
                        if not fut.done():
                            fut.cancel()
                    self._pending.clear()
                    await self._authenticate()
                    await self._subscribe_base()
                    log.info("Connected and subscribed")
                    async for raw in ws:
                        await self._handle_message(raw)
            except Exception as e:
                log.warning(f"WS disconnected: {type(e).__name__}: {e} — reconnecting in 5s")
                self.ws = None
                await asyncio.sleep(5)

    async def watchdog(self):
        while self._running:
            await asyncio.sleep(60)
            if self._last_price_ts and (time.time() - self._last_price_ts) > 90:
                log.warning("Watchdog: no price in 90s — forcing reconnect")
                if self.ws:
                    try: await self.ws.close()
                    except: pass

    def add_callback(self, cb):
        self._callbacks.append(cb)

    async def subscribe_orderbook(self, instrument):
        channel = f"book.{instrument}.none.20.100ms"
        await self._send("public/subscribe", {"channels": [channel]})

    def get_orderbook(self, instrument):
        return self._orderbooks.get(instrument, {})

    async def get_instruments(self, kind="option", expired=False):
        return await self._send("public/get_instruments", {
            "currency": "ETH", "kind": kind, "expired": expired,
        })

    async def get_ticker(self, instrument):
        return await self._send("public/ticker", {"instrument_name": instrument})

    def estimate_fill_price(self, instrument, side, qty):
        ob = self._orderbooks.get(instrument, {})
        if not ob or not ob.get("bids") or not ob.get("asks"):
            return None, None, 0
        bids, asks = ob["bids"], ob["asks"]
        mid = (bids[0][0] + asks[0][0]) / 2 if bids and asks else None
        if not mid: return None, None, 0
        levels = asks if side == "buy" else bids
        remaining, total_cost, total_filled = qty, 0.0, 0.0
        for price, size in levels:
            fill_qty = min(remaining, size)
            total_cost += fill_qty * price
            total_filled += fill_qty
            remaining -= fill_qty
            if remaining <= 0: break
        if total_filled == 0: return None, None, 0
        avg_fill = total_cost / total_filled
        return avg_fill, abs(avg_fill - mid), total_filled

    async def disconnect(self):
        self._running = False
        if self.ws:
            await self.ws.close()
