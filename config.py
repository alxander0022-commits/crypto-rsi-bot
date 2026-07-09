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
RSI_BUY          = 30            # UPTREND + RSI below this  -> 🟢 BUY the dip
RSI_SELL         = 80            # DOWNTREND + RSI above this -> 🔴 SELL the top
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

# Position tracking (/buy /sell + ledger P&L) is kept for your manual paper
# record only — these no longer drive alerts.
TARGET_PCT        = 0.03         # shown in /status only
DISASTER_STOP_ON  = False        # unused by the current model
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
