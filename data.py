"""
Public market-data access via Kraken (no API key needed for candles).

Why Kraken and not Bybit: Bybit returns HTTP 403 to US datacenter IPs (where
GitHub Actions runs), so the cloud bot couldn't fetch prices. Kraken serves
public OHLC data to datacenter IPs reliably. Same OHLC data, ~same prices.

Docs: https://docs.kraken.com/rest/#operation/getOHLCData
The get_klines() interface is unchanged, so the rest of the bot needs no edits.
"""

import time

import requests
import pandas as pd

KRAKEN_OHLC = "https://api.kraken.com/0/public/OHLC"
RETRIES = 3          # transient network blips shouldn't drop a coin
RETRY_WAIT = 3       # seconds between attempts

# Bybit interval code -> Kraken interval (minutes)
_INTERVAL_MIN = {"60": 60, "240": 240, "D": 1440, "W": 10080}


def _kraken_pair(symbol):
    """BTCUSDT -> XBTUSD, ETHUSDT -> ETHUSD, SOLUSDT -> SOLUSD, ..."""
    base = symbol.upper().replace("USDT", "").replace("USD", "")
    if base == "BTC":
        base = "XBT"          # Kraken uses XBT for Bitcoin
    return base + "USD"


def get_klines(symbol, interval, category="spot", limit=400):
    """
    Fetch OHLC candles from Kraken and return a DataFrame sorted oldest->newest
    with float open/high/low/close/volume columns and a UTC `start` column.

    interval: Bybit-style code ("60" = 1h, "240" = 4h, "D" = daily). `category`
    is accepted for compatibility but ignored. Kraken returns up to 720 candles
    (plenty for MA200); `limit` just trims the tail if smaller.
    """
    kint = _INTERVAL_MIN.get(str(interval))
    if kint is None:
        raise RuntimeError(f"Unsupported interval {interval!r}")
    pair = _kraken_pair(symbol)
    params = {"pair": pair, "interval": kint}

    last_err = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(KRAKEN_OHLC, params=params, timeout=20)
            r.raise_for_status()
            payload = r.json()
            if payload.get("error"):
                raise RuntimeError(f"Kraken error: {payload['error']}")
            break
        except (requests.exceptions.RequestException, RuntimeError) as e:
            last_err = e
            if attempt < RETRIES - 1:
                time.sleep(RETRY_WAIT)
    else:
        raise last_err

    result = payload["result"]
    # result maps a (normalized) pair name -> rows, plus a "last" cursor
    key = next((k for k in result if k != "last"), None)
    rows = result.get(key) if key else None
    if not rows:
        raise RuntimeError(f"No candles for {symbol} ({pair}) @ {interval}")

    # Kraken row: [time, open, high, low, close, vwap, volume, count]
    df = pd.DataFrame(
        rows,
        columns=["start", "open", "high", "low", "close", "vwap", "volume", "count"],
    )
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["start"] = pd.to_datetime(df["start"].astype("int64"), unit="s", utc=True)

    df = df.sort_values("start").reset_index(drop=True)  # oldest first
    if limit and len(df) > limit:
        df = df.tail(limit).reset_index(drop=True)
    return df
