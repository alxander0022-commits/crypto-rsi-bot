"""
Appends one row to the CSV paper-trading ledger per hourly check.
"""

import csv
import os

import config

HEADER = ["timestamp", "symbol", "price", "rsi", "trend", "adx", "signal", "action"]

TRADES_HEADER = ["opened", "closed", "symbol", "side", "entry", "exit",
                 "qty", "pnl_usd", "pnl_pct", "reason"]


def append(row):
    exists = os.path.exists(config.LEDGER_FILE)
    with open(config.LEDGER_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(HEADER)
        writer.writerow(row)


def append_trade(row):
    """Append one closed paper trade to trades.csv."""
    exists = os.path.exists(config.TRADES_FILE)
    with open(config.TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(TRADES_HEADER)
        writer.writerow(row)


def read_trades(limit=None):
    """Return closed trades as a list of dicts (newest last)."""
    if not os.path.exists(config.TRADES_FILE):
        return []
    with open(config.TRADES_FILE, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-limit:] if limit else rows
