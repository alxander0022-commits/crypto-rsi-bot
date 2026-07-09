"""
Bybit public market-data access. No API key needed for candles.
Docs: https://bybit-exchange.github.io/docs/v5/market/kline
"""

import time

import requests
import pandas as pd

BYBIT_BASE = "https://api.bybit.com"
RETRIES = 3          # transient DNS/network blips shouldn't drop a coin
RETRY_WAIT = 3       # seconds between attempts


def get_klines(symbol, interval, category="spot", limit=400):
    """
    Fetch OHLCV candles from Bybit and return a DataFrame sorted oldest->newest.

    interval: Bybit code. "60" = 1h, "240" = 4h, "D" = daily.
    Retries a few times on transient network errors before giving up.
    """
    url = f"{BYBIT_BASE}/v5/market/kline"
    params = {
        "category": category,
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }

    last_err = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt < RETRIES - 1:
                time.sleep(RETRY_WAIT)
    else:
        raise last_err

    payload = r.json()

    if payload.get("retCode") != 0:
        raise RuntimeError(f"Bybit API error: {payload.get('retMsg')}")

    rows = payload["result"]["list"]  # Bybit returns newest-first
    if not rows:
        raise RuntimeError(f"No candles returned for {symbol} @ {interval}")

    df = pd.DataFrame(
        rows,
        columns=["start", "open", "high", "low", "close", "volume", "turnover"],
    )
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = df[col].astype(float)
    df["start"] = pd.to_datetime(df["start"].astype("int64"), unit="ms", utc=True)

    # oldest first so indicator libraries compute forward in time
    df = df.sort_values("start").reset_index(drop=True)
    return df
