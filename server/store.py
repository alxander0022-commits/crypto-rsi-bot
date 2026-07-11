"""
SQLite persistence for the trading server: panel settings, closed trades,
and small runtime state (paused flag, last-seen positions, engine heartbeat).
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "tradebot.db")

# Panel-editable settings and their defaults. All money in USDT.
DEFAULTS = {
    "allocated_capital": 1000.0,   # the ONLY money the bot may use
    "trade_fraction": 0.20,        # fraction of allocated capital per trade
    "stop_pct": 0.02,              # initial stop-loss distance
    "exit_mode": "split",          # "take_profit" | "trailing" | "split"
    "take_profit_pct": 0.03,       # fixed take-profit target (tp & split modes)
    "trail_activate_pct": 0.05,    # arm the trailing stop at +5%
    "trail_distance_pct": 0.04,    # trail 4% off the peak
    "rsi_buy": 30.0,
    "rsi_sell": 70.0,
    "leverage": 1,                 # 1-10x; multiplies position size (classic)
    "one_at_a_time": True,         # only ONE open position across all coins
    "max_positions": 3,            # cap when one_at_a_time is off
    "max_daily_loss_pct": 0.05,    # pause after -5% of allocated in a day
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "paused": False,
    "live_confirmed": False,       # must be set via GO-LIVE in the panel
}


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            closed_at TEXT NOT NULL,      -- ISO UTC
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,           -- Buy/Sell (the entry side)
            qty REAL, entry REAL, exit_price REAL,
            pnl REAL NOT NULL,
            exchange_id TEXT UNIQUE       -- Bybit orderId, dedups sync
        )""")
        # migration: leverage column (added 2026-07-11)
        try:
            c.execute("ALTER TABLE trades ADD COLUMN leverage REAL")
        except sqlite3.OperationalError:
            pass  # already exists


def get_settings():
    init()
    with _conn() as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM kv")}
    out = {}
    for k, default in DEFAULTS.items():
        out[k] = json.loads(rows[k]) if k in rows else default
    return out


def save_settings(updates):
    """Persist only known keys; values must already be validated."""
    init()
    with _conn() as c:
        for k, v in updates.items():
            if k in DEFAULTS:
                c.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          (k, json.dumps(v)))


def set_runtime(key, value):
    """Free-form runtime state (prefixed to avoid clashing with settings)."""
    init()
    with _conn() as c:
        c.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (f"rt_{key}", json.dumps(value)))


def get_runtime(key, default=None):
    init()
    with _conn() as c:
        row = c.execute("SELECT value FROM kv WHERE key = ?", (f"rt_{key}",)).fetchone()
    return json.loads(row["value"]) if row else default


def record_trade(closed_at, symbol, side, qty, entry, exit_price, pnl, exchange_id,
                 leverage=None):
    """Insert a closed trade; ignore duplicates (same exchange order id)."""
    init()
    with _conn() as c:
        c.execute("""INSERT OR IGNORE INTO trades
            (closed_at, symbol, side, qty, entry, exit_price, pnl, exchange_id, leverage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (closed_at, symbol, side, qty, entry, exit_price, pnl, exchange_id, leverage))
        return c.execute("SELECT changes()").fetchone()[0] > 0


def trades(limit=50):
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


def stats():
    init()
    with _conn() as c:
        row = c.execute("""SELECT COUNT(*) n,
            COALESCE(SUM(pnl), 0) total,
            COALESCE(SUM(CASE WHEN pnl >= 0 THEN 1 ELSE 0 END), 0) wins
            FROM trades""").fetchone()
    n, total, wins = row["n"], row["total"], row["wins"]
    return {"n_trades": n, "wins": wins, "losses": n - wins,
            "win_rate": round(wins / n * 100, 1) if n else 0.0,
            "total_pnl": round(total, 2)}


def pnl_today():
    """Realized PnL since 00:00 UTC (drives the daily kill switch)."""
    init()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _conn() as c:
        row = c.execute("SELECT COALESCE(SUM(pnl), 0) s FROM trades "
                        "WHERE closed_at >= ?", (day,)).fetchone()
    return float(row["s"])
