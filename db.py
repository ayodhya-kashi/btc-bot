import sqlite3, json, time
from config import DB_FILE

def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        open_time       REAL,
        close_time      REAL,
        status          TEXT,      -- OPEN | CLOSED_TP | CLOSED_SL | CLOSED_EXPIRY
        eth_price_entry REAL,
        eth_price_exit  REAL,

        -- call leg
        call_strike     REAL,
        call_expiry     TEXT,
        call_delta_entry REAL,
        call_premium    REAL,      -- mid at entry (after slippage)
        call_premium_exit REAL,

        -- put leg
        put_strike      REAL,
        put_expiry      TEXT,
        put_delta_entry REAL,
        put_premium     REAL,
        put_premium_exit REAL,

        -- combined
        total_premium_collected REAL,
        take_profit_target      REAL,
        stop_loss_threshold     REAL,

        -- hedge tracking (JSON list of hedge events)
        hedge_log       TEXT,      -- [{time, eth_price, qty, direction, slippage_usd}]
        hedge_pnl_usd   REAL,

        -- outcome
        option_pnl_usd  REAL,
        total_pnl_usd   REAL,
        pnl_pct         REAL,

        -- dvol at entry
        dvol_entry      REAL,
        notes           TEXT
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS market_snapshots (
        ts          REAL PRIMARY KEY,
        eth_price   REAL,
        dvol        REAL,
        eth_iv_atm  REAL,
        bid_ask_spread_perp REAL
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS hedge_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id    INTEGER,
        ts          REAL,
        eth_price   REAL,
        qty_eth     REAL,      -- positive = bought, negative = sold
        direction   TEXT,      -- BUY | SELL
        slippage_usd REAL,
        cumulative_delta_after REAL,
        FOREIGN KEY(trade_id) REFERENCES trades(id)
    )""")

    conn.commit()
    conn.close()

def insert_trade(trade: dict) -> int:
    conn = get_conn()
    c = conn.cursor()
    cols = ", ".join(trade.keys())
    placeholders = ", ".join(["?"] * len(trade))
    c.execute(f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
              list(trade.values()))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id

def update_trade(trade_id: int, updates: dict):
    conn = get_conn()
    c = conn.cursor()
    set_clause = ", ".join([f"{k}=?" for k in updates.keys()])
    c.execute(f"UPDATE trades SET {set_clause} WHERE id=?",
              list(updates.values()) + [trade_id])
    conn.commit()
    conn.close()

def insert_hedge_event(event: dict):
    conn = get_conn()
    c = conn.cursor()
    cols = ", ".join(event.keys())
    placeholders = ", ".join(["?"] * len(event))
    c.execute(f"INSERT INTO hedge_events ({cols}) VALUES ({placeholders})",
              list(event.values()))
    conn.commit()
    conn.close()

def upsert_snapshot(snap: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO market_snapshots (ts, eth_price, dvol, eth_iv_atm, bid_ask_spread_perp)
        VALUES (:ts, :eth_price, :dvol, :eth_iv_atm, :bid_ask_spread_perp)
    """, snap)
    conn.commit()
    conn.close()

def get_all_trades():
    conn = get_conn()
    c = conn.cursor()
    rows = c.execute("SELECT * FROM trades ORDER BY open_time DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_open_trades():
    conn = get_conn()
    c = conn.cursor()
    rows = c.execute("SELECT * FROM trades WHERE status='OPEN' ORDER BY open_time DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_hedge_events(trade_id: int):
    conn = get_conn()
    c = conn.cursor()
    rows = c.execute("SELECT * FROM hedge_events WHERE trade_id=? ORDER BY ts", (trade_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_latest_snapshot():
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT * FROM market_snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else {}

def get_summary_stats():
    conn = get_conn()
    c = conn.cursor()
    stats = {}
    row = c.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status != 'OPEN' THEN 1 ELSE 0 END) as closed,
            SUM(CASE WHEN total_pnl_usd > 0 THEN 1 ELSE 0 END) as winners,
            SUM(CASE WHEN total_pnl_usd < 0 THEN 1 ELSE 0 END) as losers,
            SUM(COALESCE(total_pnl_usd, 0)) as total_pnl,
            AVG(CASE WHEN status != 'OPEN' THEN total_pnl_usd END) as avg_pnl,
            MAX(total_pnl_usd) as best_trade,
            MIN(total_pnl_usd) as worst_trade
        FROM trades
    """).fetchone()
    conn.close()
    return dict(row) if row else {}
