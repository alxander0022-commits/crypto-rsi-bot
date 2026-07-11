"""
Historical data access for the standalone backtest (read-only, public API).

- Klines (D / 60 / 15): fetched once per (symbol, interval, window) and cached
  as CSV under research/data/.
- 1-minute klines: fetched lazily in aligned 1000-minute chunks while a
  simulated trade is open (exit simulation), cached per chunk on disk.
- Funding-rate history: fetched per symbol, cached.

All rows are returned OLDEST-FIRST as dicts with float OHLC and int ms start.
The still-forming candle is always dropped (start + interval > now).
"""

import csv
import os
import time

import requests

BASE = "https://api.bybit.com"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

INTERVAL_MS = {"1": 60_000, "15": 900_000, "60": 3_600_000, "D": 86_400_000}

_session = requests.Session()


def _get(path, params, retries=4):
    for attempt in range(retries):
        try:
            r = _session.get(f"{BASE}{path}", params=params, timeout=25)
            r.raise_for_status()
            data = r.json()
            if data.get("retCode") != 0:
                raise RuntimeError(f"Bybit {path}: {data.get('retMsg')}")
            return data["result"]
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def _rows_to_candles(rows):
    """Bybit kline row -> dict (rows arrive newest-first; caller sorts)."""
    return [{
        "start": int(r[0]),
        "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]),
        "volume": float(r[5]),
    } for r in rows]


def _fetch_klines(symbol, interval, start_ms, end_ms):
    """Paginate backwards from end_ms until start_ms. Oldest-first result."""
    out = {}
    cursor_end = end_ms
    while True:
        res = _get("/v5/market/kline", {
            "category": "linear", "symbol": symbol, "interval": interval,
            "start": start_ms, "end": cursor_end, "limit": 1000,
        })
        rows = res.get("list") or []
        if not rows:
            break
        for c in _rows_to_candles(rows):
            out[c["start"]] = c
        oldest = min(int(r[0]) for r in rows)
        if oldest <= start_ms or len(rows) < 1000:
            break
        cursor_end = oldest - 1
        time.sleep(0.15)  # be polite
    candles = sorted(out.values(), key=lambda c: c["start"])
    # drop the still-forming candle
    now_ms = int(time.time() * 1000)
    step = INTERVAL_MS[interval]
    return [c for c in candles if c["start"] + step <= now_ms]


def _cache_path(name):
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, name)


def _save_csv(path, candles):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["start", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c["start"], c["open"], c["high"], c["low"], c["close"], c["volume"]])


def _load_csv(path):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return [{
            "start": int(row["start"]),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]),
        } for row in csv.DictReader(f)]


def klines(symbol, interval, start_ms, end_ms):
    """Cached window fetch (D/60/15). Oldest-first, closed candles only."""
    name = f"{symbol}_{interval}_{start_ms}_{end_ms}.csv"
    path = _cache_path(name)
    if os.path.exists(path):
        return _load_csv(path)
    candles = _fetch_klines(symbol, interval, start_ms, end_ms)
    _save_csv(path, candles)
    return candles


class OneMinuteStore:
    """Lazy 1m candles in aligned 1000-minute chunks, disk+memory cached."""

    CHUNK_MS = 1000 * 60_000

    def __init__(self):
        self._mem = {}   # (symbol, chunk_start) -> list[candle]

    def _chunk(self, symbol, chunk_start):
        key = (symbol, chunk_start)
        if key in self._mem:
            return self._mem[key]
        path = _cache_path(f"{symbol}_1m_{chunk_start}.csv")
        if os.path.exists(path):
            candles = _load_csv(path)
        else:
            candles = _fetch_klines(symbol, "1", chunk_start,
                                    chunk_start + self.CHUNK_MS - 1)
            _save_csv(path, candles)
        self._mem[key] = candles
        return candles

    def get(self, symbol, start_ms, end_ms):
        """All closed 1m candles with start in [start_ms, end_ms], oldest-first.
        Returns None if the range can't be served (data gap)."""
        first = (start_ms // self.CHUNK_MS) * self.CHUNK_MS
        out = []
        chunk = first
        while chunk <= end_ms:
            out.extend(self._chunk(symbol, chunk))
            chunk += self.CHUNK_MS
        got = [c for c in out if start_ms <= c["start"] <= end_ms]
        return got or None


def funding_history(symbol, start_ms, end_ms):
    """[{ts, rate}] oldest-first, cached. Raises if unavailable."""
    path = _cache_path(f"{symbol}_funding_{start_ms}_{end_ms}.csv")
    if os.path.exists(path):
        with open(path, "r", newline="", encoding="utf-8") as f:
            return [{"ts": int(r["ts"]), "rate": float(r["rate"])}
                    for r in csv.DictReader(f)]
    out = {}
    cursor_end = end_ms
    while True:
        res = _get("/v5/market/funding/history", {
            "category": "linear", "symbol": symbol,
            "startTime": start_ms, "endTime": cursor_end, "limit": 200,
        })
        rows = res.get("list") or []
        if not rows:
            break
        for r in rows:
            ts = int(r["fundingRateTimestamp"])
            out[ts] = float(r["fundingRate"])
        oldest = min(int(r["fundingRateTimestamp"]) for r in rows)
        if oldest <= start_ms or len(rows) < 200:
            break
        cursor_end = oldest - 1
        time.sleep(0.15)
    entries = [{"ts": ts, "rate": rate} for ts, rate in sorted(out.items())]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "rate"])
        for e in entries:
            w.writerow([e["ts"], e["rate"]])
    return entries
