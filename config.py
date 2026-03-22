# ═══════════════════════════════════════════════════════════════
#  DERIBIT PAPER TRADING BOT — CONFIG
#  Credentials live in .env — never gets overwritten by updates.
#  All other settings are tunable here.
# ═══════════════════════════════════════════════════════════════
import os

def _env(key, default=None):
    val = os.environ.get(key, default)
    if val is None:
        raise RuntimeError(f"Missing required env var: {key}. Add it to .env")
    return val

# load .env file manually (no external library needed)
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# ── Deribit API ──────────────────────────────────────────────────────────────
DERIBIT_CLIENT_ID     = _env("DERIBIT_CLIENT_ID")
DERIBIT_CLIENT_SECRET = _env("DERIBIT_CLIENT_SECRET")
DERIBIT_WS_URL        = "wss://www.deribit.com/ws/api/v2"

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = _env("TELEGRAM_CHAT_ID", "")

# ── Iron Condor parameters ───────────────────────────────────────────────────
SHORT_DELTA           = 0.20      # sell ~20-delta strikes each side
WING_WIDTH_PCT        = 0.04      # long strikes 4% further OTM than short
DVOL_MIN              = 45.0
DVOL_MAX = 85.0
ENTRY_HOUR_UTC        = 9
ENTRY_HOUR_UTC_END    = 11
STOP_LOSS_MULT        = 2.0       # close if loss > 2× premium received

# ── Slippage model ───────────────────────────────────────────────────────────
SLIPPAGE_OPTIONS_PCT  = 0.003     # applied across all 4 legs

# ── Capital & sizing ─────────────────────────────────────────────────────────
PAPER_CAPITAL_USD     = 5000.0
MAX_RISK_PER_TRADE    = 0.05      # max 5% per condor (~$250 max loss)

# ── Dashboard ────────────────────────────────────────────────────────────────
DASHBOARD_HOST        = "0.0.0.0"
DASHBOARD_PORT = 8081
DASHBOARD_REFRESH_SEC = 5

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE = "logs/bot.log"
DB_FILE  = "data/trades.db"
