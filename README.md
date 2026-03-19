# ETH Deribit Paper Trading Bot — Setup Guide

## What this does
- Connects to Deribit's live WebSocket API (read-only)
- Scouts for daily theta trades (1DTE ETH short strangles)
- Simulates full trade lifecycle: entry → delta hedge → TP/SL → exit
- Tracks order-book slippage on perp hedges realistically
- Shows live dashboard in browser + Telegram alerts
- Never places a real order. 100% paper only.

---

## Step 1 — Upload files to your VPS

From your local machine:
```bash
scp -r deribit_bot/ user@YOUR_VPS_IP:~/
```

Or on the VPS directly, create the folder and paste each file.

---

## Step 2 — Install Python dependencies

SSH into your VPS, then:
```bash
cd ~/deribit_bot
pip3 install -r requirements.txt
```

If pip3 isn't installed:
```bash
sudo apt update && sudo apt install python3-pip -y
```

---

## Step 3 — Fill in config.py

Open config.py and replace the placeholder values:
```bash
nano config.py
```

**Deribit API keys** (read-only is fine):
- Log in to deribit.com → Account → API → Create Key
- Permissions needed: Read only
- Paste client_id and client_secret into config.py

**Telegram Bot**:
1. Message @BotFather on Telegram → /newbot → follow steps → copy token
2. Message @userinfobot on Telegram → it sends you your chat_id
3. Paste both into config.py

Save: Ctrl+O → Enter → Ctrl+X

---

## Step 4 — Test run (see output in terminal)

```bash
cd ~/deribit_bot
python3 main.py
```

You should see:
```
[main] INFO Bot starting up
[main] INFO Database ready
[main] INFO Dashboard running at http://0.0.0.0:8080
[deribit_ws] INFO Authenticated with Deribit
[deribit_ws] INFO Subscribed to base channels
```

Open your browser: http://YOUR_VPS_IP:8080

The bot will wait until 10:00–12:00 UTC and DVOL is in range before
scouting its first trade. You can watch the dashboard update in real-time.

Stop with Ctrl+C.

---

## Step 5 — Run forever with systemd (auto-restart on crash/reboot)

```bash
# Edit the service file — replace YOUR_LINUX_USERNAME with your actual username
nano deribit-bot.service

# Copy to systemd
sudo cp deribit-bot.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable deribit-bot
sudo systemctl start deribit-bot

# Check it's running
sudo systemctl status deribit-bot

# Watch live logs
sudo journalctl -u deribit-bot -f
```

---

## Step 6 — Open firewall for dashboard (if needed)

If you can't reach port 8080 from your browser:
```bash
sudo ufw allow 8080/tcp
sudo ufw reload
```

Then visit: http://YOUR_VPS_IP:8080

---

## File overview

| File | What it does |
|------|-------------|
| config.py | All your settings — edit this |
| main.py | Entry point — run this |
| deribit_client.py | Deribit WebSocket connection |
| strategy.py | Trade logic, entry scouting, hedge management |
| dashboard.py | Flask web UI |
| db.py | SQLite database (trades stored in data/trades.db) |
| telegram_notify.py | Telegram alerts |
| deribit-bot.service | Systemd service for auto-restart |

---

## Reading the dashboard

- **Green DVOL** = in range (45–80), bot will trade
- **Amber DVOL** = outside range, bot is watching but not trading
- **Open Positions** = current live simulated trades
- **Hedge Events** = every perp hedge that fired, with slippage
- **Net P&L** = option P&L + hedge P&L − all slippage costs

---

## Tuning the strategy (config.py)

| Parameter | Default | What it controls |
|-----------|---------|-----------------|
| TARGET_DELTA | 0.20 | How far OTM your strikes are |
| DVOL_MIN | 45 | Don't trade in low-vol regimes |
| DVOL_MAX | 80 | Don't trade in panic spikes |
| TAKE_PROFIT_PCT | 0.60 | Close at 60% of premium |
| STOP_LOSS_MULT | 2.0 | Close if loss > 2× premium |
| REHhedge_THRESHOLD | 0.015 | Re-hedge every 1.5% ETH move |
| MAX_RISK_PER_TRADE | 0.15 | Max 15% of capital per trade |

After 20–30 paper trades, the logged data will tell you which parameters
to tighten or loosen before considering live deployment.

---

## Common issues

**"No 1DTE expiry found"** — Deribit occasionally has gaps in daily
expiries. The bot logs this and retries next tick. Normal.

**"Zero mid price"** — Strike is illiquid. Bot skips, tries next day.

**Dashboard not loading** — Check firewall (Step 6) and that the bot
is actually running (Step 5 status command).

**Telegram not sending** — Double-check bot token and chat_id in config.
Test by messaging your bot on Telegram first.
