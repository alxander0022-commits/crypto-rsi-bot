"""
Appends one row to the CSV paper-trading ledger per hourly check.
"""

import csv
import os

import config

HEADER = ["timestamp", "symbol", "price", "rsi", "trend", "adx", "signal", "action"]


def append(row):
    exists = os.path.exists(config.LEDGER_FILE)
    with open(config.LEDGER_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(HEADER)
        writer.writerow(row)
