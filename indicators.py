"""
Indicator calculations. All heavy lifting is delegated to the `ta` library
(https://github.com/bukosabino/ta) — RSI, moving averages and ADX are NOT
hand-coded, as required.
"""

import math

from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, ADXIndicator

import config


def latest_rsi(df_1h):
    """RSI(14) on the entry (1-hour) timeframe, latest value."""
    rsi = RSIIndicator(close=df_1h["close"], window=config.RSI_PERIOD).rsi()
    return float(rsi.iloc[-1])


def trend_state(df_trend):
    """
    Determine trend on the trend timeframe (default daily).

    Returns (direction, adx, price, ma_fast, ma_slow) where direction is one
    of "UPTREND" / "DOWNTREND" / "SIDEWAYS".

    Rules:
      price > MA200 AND MA50 > MA200  -> UPTREND
      price < MA200 AND MA50 < MA200  -> DOWNTREND
      anything else                   -> SIDEWAYS
      ADX < ADX_MIN overrides to SIDEWAYS (no real trend).
    """
    close = df_trend["close"]

    ma_fast_series = SMAIndicator(close, window=config.MA_FAST).sma_indicator()
    ma_slow_series = SMAIndicator(close, window=config.MA_SLOW).sma_indicator()
    adx_series = ADXIndicator(
        high=df_trend["high"],
        low=df_trend["low"],
        close=close,
        window=config.ADX_PERIOD,
    ).adx()

    price = float(close.iloc[-1])
    ma_fast = float(ma_fast_series.iloc[-1])
    ma_slow = float(ma_slow_series.iloc[-1])
    adx = float(adx_series.iloc[-1])

    # If we don't have enough history the values come back NaN. Treat a NaN
    # ADX as 0 (forces SIDEWAYS) and let NaN MA comparisons fall through to
    # SIDEWAYS as well.
    if math.isnan(adx):
        adx = 0.0

    if price > ma_slow and ma_fast > ma_slow:
        direction = "UPTREND"
    elif price < ma_slow and ma_fast < ma_slow:
        direction = "DOWNTREND"
    else:
        direction = "SIDEWAYS"

    if adx < config.ADX_MIN:
        direction = "SIDEWAYS"

    return direction, adx, price, ma_fast, ma_slow
