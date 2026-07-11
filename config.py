"""
Configuration for the RSI + Trend alert bot.
Everything you'd normally tweak lives here at the top.

Telegram credentials are read from environment variables first (so you can
use GitHub Actions secrets), and fall back to the literals below if you'd
rather just paste them in for local running.
"""

import os

# ─── MARKET ───────────────────────────────────────────────────────────
# Coins to watch. Each is checked every hour and alerts independently —
# a signal in ANY of them pings you. Add/remove freely.
SYMBOLS          = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CATEGORY         = "spot"        # Bybit product category: "spot" or "linear"
ENTRY_TIMEFRAME  = "60"          # 1-hour candles for RSI (Bybit interval code)
TREND_TIMEFRAME  = "D"           # trend timeframe. "D" = daily, "240" = 4H

# ─── INDICATORS ───────────────────────────────────────────────────────
RSI_PERIOD       = 14
RSI_BUY          = 30            # UPTREND + RSI crosses back up through this  -> 🟢 BUY
RSI_SELL         = 70            # DOWNTREND + RSI crosses back down through this -> 🔴 SELL
# Confirmation: only signal when RSI has gone past the level and then crossed
# BACK through it (the extreme is rolling over) — avoids acting while a spike is
# still running. Set False for the simple "RSI is beyond the level" trigger.
CONFIRM_REVERSAL = True
# Heads-up: warn "setup building" when RSI is within this many points of the
# level (in the right trend) but hasn't triggered yet.
SETUP_WARN       = 5
MA_FAST          = 50            # fast moving average (periods)
MA_SLOW          = 200           # slow moving average (periods)
ADX_PERIOD       = 14
ADX_MIN          = 20            # ADX below this = no real trend -> SIDEWAYS

# ─── STRATEGY ─────────────────────────────────────────────────────────
# Every hour the bot sends ONE message, one sentence per coin, with the coin's
# trend, RSI and a plain verdict:
#   UPTREND   + RSI < RSI_BUY   -> 🟢 time to BUY
#   DOWNTREND + RSI > RSI_SELL  -> 🔴 time to SELL
#   otherwise                   -> ⛔ stay out

# ─── PAPER AUTO-TRADER (simulated money — NEVER touches an exchange) ───
PAPER_TRADING    = True          # auto-open/close simulated trades on signals
START_BALANCE    = 10000.0       # starting virtual account (USD)
TRADE_FRACTION   = 0.20          # fraction of equity to put in each trade
STOP_PCT         = 0.02          # initial stop-loss distance from entry (2%)
TARGET_PCT       = 0.03          # fixed take-profit (used only when TRAIL_ON = False)
ALLOW_SHORTS     = True          # allow simulated SHORTs on 🔴 SELL signals

# Trailing stop — let winners run. Once a trade is +TRAIL_ACTIVATE_PCT in
# profit, a stop trails TRAIL_DISTANCE_PCT below the best price reached: the
# trade keeps running while the trend continues and exits only when it reverses,
# locking in at least (TRAIL_ACTIVATE_PCT - TRAIL_DISTANCE_PCT) profit.
# Example below: ride from +5%, trail 4% off the peak -> ~1% floor once armed.
TRAIL_ON            = True
TRAIL_ACTIVATE_PCT  = 0.05       # arm the trailing stop after +5% profit
TRAIL_DISTANCE_PCT  = 0.04       # then exit if price falls 4% from its peak
TRADES_FILE      = "trades.csv"  # closed-trade log
DATA_FILE        = "docs/data.json"  # dashboard data written each run

# (legacy manual-position fields, unused by the auto-trader)
DISASTER_STOP_ON  = False
DISASTER_STOP_PCT = 0.15

# ─── 12-HOUR REPORT ───────────────────────────────────────────────────
NEWS_REPORT_ON    = True         # send the 12h report (Fear & Greed gauge + chart)
# AI market commentary is OFF (keeps the bot 100% free). Set True + add an
# ANTHROPIC_API_KEY to also get the AI-written buying/selling/panicking read.
NEWS_MACRO_ON     = False
NEWS_MODEL        = "claude-opus-4-8"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "PASTE_YOUR_ANTHROPIC_KEY_HERE")
NEWS_LOG_FILE     = "news_log.txt"

# ─── TELEGRAM ─────────────────────────────────────────────────────────
# Environment variables win (great for GitHub Actions secrets); otherwise
# the pasted literals are used.
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "PASTE_YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "PASTE_YOUR_CHAT_ID_HERE")

# ─── FILES ────────────────────────────────────────────────────────────
STATE_FILE       = "state.json"  # remembers your position between runs
LEDGER_FILE      = "ledger.csv"  # paper-trading record, one row per hour

# How many candles to pull. Needs to comfortably exceed MA_SLOW so the
# 200-period MA and ADX are stable. 400 is plenty for daily.
KLINE_LIMIT      = 400
