"""
ETH Deribit Paper Trading Bot — Main Entry Point
Run: python main.py
Stop: Ctrl+C
"""
import asyncio, logging, threading, time, sys
from logging.handlers import RotatingFileHandler

import db
from config import LOG_FILE, DASHBOARD_HOST, DASHBOARD_PORT
from deribit_client import DeribitClient
from telegram_notify import TelegramNotifier
from strategy import StrategyEngine
import dashboard

# ── Logging setup ─────────────────────────────────────────────────────────────
def setup_logging():
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

log = logging.getLogger("main")

# ── Daily summary scheduler ───────────────────────────────────────────────────
async def daily_summary_loop(telegram: TelegramNotifier):
    """Send Telegram summary every 24h at 08:30 UTC."""
    while True:
        now = time.gmtime()
        # sleep until next 08:30 UTC
        secs_to_830 = ((8 * 60 + 30) - (now.tm_hour * 60 + now.tm_min)) * 60 - now.tm_sec
        if secs_to_830 < 0:
            secs_to_830 += 86400
        await asyncio.sleep(secs_to_830)
        stats = db.get_summary_stats()
        await telegram.send_daily_summary(stats)

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    setup_logging()
    log.info("=" * 60)
    log.info("  ETH Deribit Paper Bot starting up")
    log.info("=" * 60)

    # init database
    db.init_db()
    log.info("Database ready")

    # init components
    client   = DeribitClient()
    telegram = TelegramNotifier()
    engine   = StrategyEngine(client, telegram)

    # wire dashboard
    dashboard.set_strategy(engine)

    # start Flask in a background thread (non-blocking)
    dash_thread = threading.Thread(
        target=dashboard.run_dashboard,
        daemon=True,
        name="dashboard",
    )
    dash_thread.start()
    log.info(f"Dashboard running at http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")

    # startup notification
    await telegram.send(
        "🤖 *ETH Paper Bot started*\n"
        f"Dashboard: `http://YOUR_VPS_IP:{DASHBOARD_PORT}`\n"
        "Watching for 1DTE strangle opportunities..."
    )

    # run all async tasks concurrently
    await asyncio.gather(
        client.connect(),          # WS market data (reconnects automatically)
        client.watchdog(),           # restart if data goes stale
        engine.run(),              # strategy loop (30s ticks)
        daily_summary_loop(telegram),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
