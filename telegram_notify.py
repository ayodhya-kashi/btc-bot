import aiohttp, logging
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger("telegram")

class TelegramNotifier:
    BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    async def send(self, text: str):
        if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith("YOUR"):
            log.info(f"[Telegram disabled] {text}")
            return
        url = f"{self.BASE}/sendMessage"
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": "Markdown",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        body = await r.text()
                        log.warning(f"Telegram error {r.status}: {body}")
        except Exception as e:
            log.warning(f"Telegram send failed: {e}")

    async def send_daily_summary(self, stats: dict):
        closed = stats.get("closed", 0)
        if closed == 0:
            return
        winners = stats.get("winners", 0)
        win_rate = winners / closed if closed else 0
        msg = (
            f"📅 *DAILY SUMMARY*\n"
            f"Trades closed: {closed}\n"
            f"Win rate: {win_rate:.0%} ({winners}W / {closed - winners}L)\n"
            f"Total P&L: ${stats.get('total_pnl', 0):.2f}\n"
            f"Avg P&L/trade: ${stats.get('avg_pnl', 0):.2f}\n"
            f"Best: ${stats.get('best_trade', 0):.2f} | Worst: ${stats.get('worst_trade', 0):.2f}"
        )
        await self.send(msg)
