"""
Standalone entry-confirmation backtest — does NOT touch the live engine.

Compares 4 entry modes under the EXACT live strategy settings:
  mode1_current   : hourly RSI cross-back on candle close
                    (SELL: prev>=rsi_sell and now<rsi_sell; BUY inverse — owner spec)
  mode2_twocandle : armed + 2 consecutive closed 15m candles in entry direction
  mode3_structure : armed + closed 15m candle through latest confirmed swing
                    (pivot_length=2, formed after arming), right color,
                    body_ratio>=0.55, 0.30<=TR/ATR14<=1.50
  mode4_break2    : a valid mode-3 break candle followed immediately by one
                    more closed 15m candle in the entry direction

Fixed (mirrors live engine): daily trend gate (MA50/MA200 + ADX>=20; UP->BUY
only, DOWN->SELL only), hourly RSI arming (30/70), disarm on trend flip /
RSI neutral +-5 / 12h timeout / entry, one position at a time portfolio-wide,
notional = allocated*fraction = $200, exits = 2% stop + split (half TP +3%,
half trailing: arm +5%, trail 4% off peak).

Costs: taker 0.055%/side + slippage 0.02%/side + historical funding.
Exits simulated on 1-minute candles (15m conservative fallback tagged
`intrabar_ambiguous`). Closed candles only everywhere; no look-ahead.

Usage:  python research/backtest.py [--months 12]
Output: research/report.md + research/trades_<mode>.csv
"""

import argparse
import csv
import os
import sys
import time
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, ADXIndicator
from ta.volatility import AverageTrueRange

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bybit_data as data

# ── fixed strategy settings (mirror the live engine) ──────────────────
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
RSI_BUY, RSI_SELL = 30.0, 70.0
NEUTRAL_BUFFER = 5.0
ARM_TIMEOUT_MS = 12 * 3600_000
ADX_MIN = 20.0
ALLOCATED, FRACTION = 1000.0, 0.20          # -> $200 notional per trade
STOP_PCT, TP_PCT = 0.02, 0.03
TRAIL_ACT, TRAIL_DIST = 0.05, 0.04
FEE, SLIP = 0.00055, 0.0002                 # per side
PIVOT_LEN = 2
MIN_BODY, MIN_BRK_ATR, MAX_BRK_ATR = 0.55, 0.30, 1.50
MODES = ["mode1_current", "mode2_twocandle", "mode3_structure", "mode4_break2"]

H1, M15, DAY = 3_600_000, 900_000, 86_400_000
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def ts_str(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def find_swings(lows, highs, pivot_len=PIVOT_LEN):
    """Strictly-confirmed swing indices. A swing low at i is strictly lower
    than the `pivot_len` candles on BOTH sides (confirmed at i+pivot_len)."""
    swing_low, swing_high = [], []
    for i in range(pivot_len, len(lows) - pivot_len):
        if all(lows[i] < lows[i - k] and lows[i] < lows[i + k]
               for k in range(1, pivot_len + 1)):
            swing_low.append(i)
        if all(highs[i] > highs[i - k] and highs[i] > highs[i + k]
               for k in range(1, pivot_len + 1)):
            swing_high.append(i)
    return swing_low, swing_high


# ── per-symbol market data with causal indicator arrays ────────────────
class Market:
    def __init__(self, symbol, start_ms, end_ms):
        self.symbol = symbol
        # warm-up fetch windows (owner clarification 1)
        d = data.klines(symbol, "D", start_ms - 260 * DAY, end_ms)
        h = data.klines(symbol, "60", start_ms - 320 * H1, end_ms)
        m = data.klines(symbol, "15", start_ms - 120 * M15, end_ms)
        self.funding = data.funding_history(symbol, start_ms, end_ms)
        self.funding_ts = [f["ts"] for f in self.funding]

        # daily trend (values are causal: index i uses candles <= i)
        dd = pd.DataFrame(d)
        sma50 = SMAIndicator(dd["close"], 50).sma_indicator()
        sma200 = SMAIndicator(dd["close"], 200).sma_indicator()
        adx = ADXIndicator(dd["high"], dd["low"], dd["close"], 14).adx()
        self.trend_close_ts, self.trend_vals = [], []
        for i in range(len(d)):
            if pd.isna(sma200.iloc[i]) or pd.isna(adx.iloc[i]):
                tr = "SIDEWAYS"
            else:
                c, f50, f200 = dd["close"].iloc[i], sma50.iloc[i], sma200.iloc[i]
                if c > f200 and f50 > f200:
                    tr = "UPTREND"
                elif c < f200 and f50 < f200:
                    tr = "DOWNTREND"
                else:
                    tr = "SIDEWAYS"
                if adx.iloc[i] < ADX_MIN:
                    tr = "SIDEWAYS"
            self.trend_close_ts.append(d[i]["start"] + DAY)   # known at close
            self.trend_vals.append(tr)

        # hourly RSI (known at candle close)
        hd = pd.DataFrame(h)
        rsi = RSIIndicator(hd["close"], 14).rsi()
        self.h_close_ts = [c["start"] + H1 for c in h]
        self.h_rsi = [float(x) if not pd.isna(x) else None for x in rsi]

        # 15m candles + ATR/body/pivots
        self.m15 = m
        self.m15_start = [c["start"] for c in m]
        md = pd.DataFrame(m)
        atr = AverageTrueRange(md["high"], md["low"], md["close"], 14).average_true_range()
        prev_close = md["close"].shift(1)
        tr_ = pd.concat([md["high"] - md["low"],
                         (md["high"] - prev_close).abs(),
                         (md["low"] - prev_close).abs()], axis=1).max(axis=1)
        self.m15_atr = [float(x) if not pd.isna(x) and x > 0 else None for x in atr]
        self.m15_tr = [float(x) if not pd.isna(x) else None for x in tr_]
        rng = (md["high"] - md["low"]).replace(0, 1e-12)
        self.m15_body = ((md["close"] - md["open"]).abs() / rng).tolist()
        self.m15_bull = (md["close"] > md["open"]).tolist()
        self.m15_bear = (md["close"] < md["open"]).tolist()

        # confirmed pivots (swing at i is CONFIRMED at index i+PIVOT_LEN;
        # strict comparison both sides; uses only candles i-2..i+2 => at
        # confirmation time all are closed -> no look-ahead at decision time)
        self.swing_low_idx, self.swing_high_idx = find_swings(
            md["low"].tolist(), md["high"].tolist())

    def trend_at(self, ts):
        i = bisect_right(self.trend_close_ts, ts) - 1
        return self.trend_vals[i] if i >= 0 else "SIDEWAYS"

    def rsi_pair_at(self, ts):
        """(prev, now) hourly RSI from candles CLOSED at or before ts."""
        i = bisect_right(self.h_close_ts, ts) - 1
        if i < 1:
            return None, None
        return self.h_rsi[i - 1], self.h_rsi[i]

    def latest_swing(self, kind, j, min_start_ms):
        """Newest confirmed swing (index) usable at 15m index j whose pivot
        candle started at/after min_start_ms. Confirmation lag = PIVOT_LEN."""
        arr = self.swing_low_idx if kind == "low" else self.swing_high_idx
        best = None
        for i in reversed(arr):
            if i + PIVOT_LEN > j:
                continue
            if self.m15_start[i] < min_start_ms:
                break
            best = i
            break
        return best


# ── exit simulation on 1m candles (owner clarification 3) ─────────────
class ExitSim:
    def __init__(self, store):
        self.store = store

    def run(self, mkt, side, entry_ts, entry_price, end_ms):
        """Simulate the live split exit. Returns trade-leg dict."""
        long = side == "BUY"
        sgn = 1 if long else -1
        notional = ALLOCATED * FRACTION
        qty = notional / entry_price
        stop = entry_price * (1 - sgn * STOP_PCT)
        tp = entry_price * (1 + sgn * TP_PCT)
        act = entry_price * (1 + sgn * TRAIL_ACT)

        state = "full"          # full -> half (after TP) ; trailing on half
        active, peak = False, None
        half_qty = qty / 2
        legs = []               # (qty, exit_price, reason, ts)
        funding_paid = 0.0
        ambiguous = False
        f_i = bisect_right(mkt.funding_ts, entry_ts)

        cursor = entry_ts
        CHUNK = 16 * 3600_000
        last_close, last_ts = entry_price, entry_ts
        while cursor < end_ms and state != "done":
            candles = self.store.get(mkt.symbol, cursor, min(cursor + CHUNK, end_ms))
            if candles is None:  # 1m gap -> conservative 15m fallback
                ambiguous = True
                j0 = bisect_right(mkt.m15_start, cursor)
                candles = mkt.m15[j0:j0 + 64]
                if not candles:
                    break
            for c in candles:
                last_close, last_ts = c["close"], c["start"]
                # funding events crossed (applies to open notional)
                while f_i < len(mkt.funding_ts) and mkt.funding_ts[f_i] <= c["start"]:
                    rate = mkt.funding[f_i]["rate"]
                    open_notional = (qty if state == "full" else half_qty) * c["open"]
                    funding_paid += sgn * rate * open_notional
                    f_i += 1
                hi, lo = c["high"], c["low"]
                up, dn = (hi, lo) if long else (lo, hi)   # favorable / adverse

                if state == "full":
                    hit_stop = lo <= stop if long else hi >= stop
                    hit_tp = hi >= tp if long else lo <= tp
                    if hit_stop and (ambiguous or not hit_tp):
                        legs.append((qty, stop, "stop", c["start"]))
                        state = "done"; break
                    if hit_stop and hit_tp:                # conservative
                        legs.append((qty, stop, "stop", c["start"]))
                        state = "done"; break
                    if hit_tp:
                        legs.append((half_qty, tp, "tp_half", c["start"]))
                        state = "half"
                        if (hi >= act if long else lo <= act):
                            active, peak = True, (hi if long else lo)
                        continue
                elif state == "half":
                    if active:
                        trail = peak * (1 - sgn * TRAIL_DIST)
                        hit_trail = lo <= trail if long else hi >= trail
                        if hit_trail:
                            legs.append((half_qty, trail, "trail", c["start"]))
                            state = "done"; break
                        peak = max(peak, hi) if long else min(peak, lo)
                    else:
                        hit_stop = lo <= stop if long else hi >= stop
                        if hit_stop:
                            legs.append((half_qty, stop, "half_stop", c["start"]))
                            state = "done"; break
                        if (hi >= act if long else lo <= act):
                            active, peak = True, (hi if long else lo)
            cursor = candles[-1]["start"] + (M15 if ambiguous else 60_000)

        if state != "done":     # end of data
            rem = qty if state == "full" else half_qty
            legs.append((rem, last_close, "eod", last_ts))

        # P&L: entry slippage+fee on full notional; per-leg exit slip+fee
        pnl = 0.0
        eff_entry = entry_price * (1 + sgn * SLIP)
        pnl -= qty * eff_entry * FEE
        trail_leg = None
        for lqty, px, reason, lts in legs:
            eff_px = px * (1 - sgn * SLIP)
            pnl += sgn * lqty * (eff_px - eff_entry)
            pnl -= lqty * eff_px * FEE
            if reason == "trail":
                trail_leg = (px, lts)
        pnl -= funding_paid

        risk = notional * STOP_PCT
        giveback = None
        if trail_leg and peak:
            giveback = abs(peak - trail_leg[0]) / entry_price * 100  # % of entry
        return {
            "side": side, "entry_ts": entry_ts, "entry": entry_price,
            "legs": legs, "pnl": pnl, "r": pnl / risk,
            "exit_ts": legs[-1][3], "funding": funding_paid,
            "ambiguous": ambiguous,
            "tp_half": any(l[2] == "tp_half" for l in legs),
            "trail_activated": active,
            "trail_exit_pct": (sgn * (trail_leg[0] - entry_price) / entry_price * 100
                               if trail_leg else None),
            "giveback_pct": giveback,
            "hold_min": (legs[-1][3] - entry_ts) / 60_000,
        }


# ── the portfolio simulation for one mode ──────────────────────────────
def run_mode(mode, markets, start_ms, end_ms, store):
    exit_sim = ExitSim(store)
    trades, missed = [], Counter()
    armed = {}                     # symbol -> dict
    busy_until = 0                 # one-position-at-a-time
    master = markets[SYMBOLS[0]].m15_start
    j0 = bisect_right(master, start_ms)
    idx_of = {s: {t: j for j, t in enumerate(markets[s].m15_start)} for s in SYMBOLS}

    def disarm(sym, reason):
        if sym in armed:
            missed[reason] += 1
            del armed[sym]

    for jm in range(j0, len(master)):
        t_close = master[jm] + M15          # this 15m candle just closed
        if t_close > end_ms:
            break

        # hourly bookkeeping, part 1 (BEFORE confirmations): safety disarms +
        # new arms. rsi_neutral disarm runs AFTER confirmations so a deep
        # one-hour RSI plunge still counts as a valid mode-1 cross-back
        # (owner's verbatim definition).
        hourly = (t_close % H1 == 0)
        if hourly:
            for sym in SYMBOLS:
                mkt = markets[sym]
                prev, now = mkt.rsi_pair_at(t_close)
                if now is None:
                    continue
                trend = mkt.trend_at(t_close)
                a = armed.get(sym)
                if a:
                    if trend != a["trend"]:
                        disarm(sym, "trend_flip")
                    elif t_close - a["ts"] > ARM_TIMEOUT_MS:
                        disarm(sym, "timeout_12h")
                if sym not in armed:
                    if trend == "DOWNTREND" and now >= RSI_SELL:
                        armed[sym] = {"side": "SELL", "ts": t_close, "trend": trend,
                                      "count": 0, "break_j": None}
                    elif trend == "UPTREND" and now <= RSI_BUY:
                        armed[sym] = {"side": "BUY", "ts": t_close, "trend": trend,
                                      "count": 0, "break_j": None}

        # entry confirmations on this closed 15m candle
        for sym in SYMBOLS:
            a = armed.get(sym)
            if not a:
                continue
            if t_close <= a["ts"]:
                continue        # only candles closed strictly AFTER arming
            mkt = markets[sym]
            j = idx_of[sym].get(master[jm])
            if j is None or j + 1 >= len(mkt.m15):
                continue
            side = a["side"]
            dir_ok = mkt.m15_bull[j] if side == "BUY" else mkt.m15_bear[j]
            confirmed = False

            if mode == "mode1_current":
                if hourly:
                    prev, now = mkt.rsi_pair_at(t_close)
                    trend = mkt.trend_at(t_close)
                    if prev is not None and trend == a["trend"]:
                        if side == "SELL" and prev >= RSI_SELL and now < RSI_SELL:
                            confirmed = True
                        if side == "BUY" and prev <= RSI_BUY and now > RSI_BUY:
                            confirmed = True
            elif mode == "mode2_twocandle":
                a["count"] = a["count"] + 1 if dir_ok else 0
                confirmed = a["count"] >= 2
            elif mode in ("mode3_structure", "mode4_break2"):
                atr, tr_ = mkt.m15_atr[j], mkt.m15_tr[j]
                brk = False
                if atr and tr_ and dir_ok and mkt.m15_body[j] >= MIN_BODY \
                        and MIN_BRK_ATR <= tr_ / atr <= MAX_BRK_ATR:
                    sw = mkt.latest_swing("low" if side == "SELL" else "high",
                                          j, a["ts"])
                    if sw is not None:
                        lvl = mkt.m15[sw]["low"] if side == "SELL" else mkt.m15[sw]["high"]
                        brk = (mkt.m15[j]["close"] < lvl if side == "SELL"
                               else mkt.m15[j]["close"] > lvl)
                if mode == "mode3_structure":
                    confirmed = brk
                else:
                    # mode4: a valid break candle must be followed IMMEDIATELY
                    # by one more candle in the entry direction.
                    if a["break_j"] == j - 1 and dir_ok:
                        confirmed = True
                        a["break_j"] = None
                    elif brk:
                        a["break_j"] = j       # fresh break, wait for next candle
                    else:
                        a["break_j"] = None    # break not followed -> expires

            if not confirmed:
                continue
            if t_close < busy_until:
                disarm(sym, "blocked_position_open")
                continue
            fill = mkt.m15[j + 1]["open"]
            trade = exit_sim.run(mkt, side, mkt.m15_start[j + 1], fill, end_ms)
            trade.update({"symbol": sym, "mode": mode,
                          "armed_ts": a["ts"],
                          "delay_min": (mkt.m15_start[j + 1] - a["ts"]) / 60_000})
            trades.append(trade)
            busy_until = trade["exit_ts"]
            del armed[sym]

        # hourly bookkeeping, part 2 (AFTER confirmations): RSI-neutral disarm
        if hourly:
            for sym in list(armed):
                a = armed[sym]
                _, now = markets[sym].rsi_pair_at(t_close)
                if now is None:
                    continue
                if (a["side"] == "SELL" and now < RSI_SELL - NEUTRAL_BUFFER) or \
                   (a["side"] == "BUY" and now > RSI_BUY + NEUTRAL_BUFFER):
                    disarm(sym, "rsi_neutral")

    return trades, missed


# ── reporting ──────────────────────────────────────────────────────────
def summarize(mode, trades, missed):
    n = len(trades)
    if n == 0:
        return {"mode": mode, "trades": 0, "missed": dict(missed)}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = -sum(t["pnl"] for t in losses)
    equity, peak_eq, max_dd = ALLOCATED, ALLOCATED, 0.0
    consec = max_consec = 0
    for t in sorted(trades, key=lambda x: x["exit_ts"]):
        equity += t["pnl"]
        peak_eq = max(peak_eq, equity)
        max_dd = max(max_dd, peak_eq - equity)
        consec = consec + 1 if t["pnl"] <= 0 else 0
        max_consec = max(max_consec, consec)
    tr_act = [t for t in trades if t["trail_activated"]]
    tr_exit = [t["trail_exit_pct"] for t in trades if t["trail_exit_pct"] is not None]
    gb = [t["giveback_pct"] for t in trades if t["giveback_pct"] is not None]
    by_side = {s: sum(t["pnl"] for t in trades if t["side"] == s) for s in ("BUY", "SELL")}
    by_sym = {s: round(sum(t["pnl"] for t in trades if t["symbol"] == s), 2) for s in SYMBOLS}
    return {
        "mode": mode, "trades": n,
        "win_rate": round(100 * len(wins) / n, 1),
        "net": round(sum(t["pnl"] for t in trades), 2),
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else float("inf"),
        "expectancy_R": round(sum(t["r"] for t in trades) / n, 3),
        "max_dd": round(max_dd, 2),
        "max_consec_losses": max_consec,
        "avg_delay_min": round(sum(t["delay_min"] for t in trades) / n, 1),
        "buy_pnl": round(by_side["BUY"], 2), "sell_pnl": round(by_side["SELL"], 2),
        "by_symbol": by_sym,
        "ambiguous": sum(1 for t in trades if t["ambiguous"]),
        "funding": round(sum(t["funding"] for t in trades), 2),
        "trail_activated_n": len(tr_act),
        "trail_avg_exit_pct": round(sum(tr_exit) / len(tr_exit), 2) if tr_exit else None,
        "trail_avg_giveback_pct": round(sum(gb) / len(gb), 2) if gb else None,
        "missed": {k: v for k, v in missed.items() if k != "entered"},
    }


def write_outputs(results, all_trades, start_ms, end_ms):
    for mode, trades in all_trades.items():
        path = os.path.join(OUT_DIR, f"trades_{mode}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "side", "entry_time", "entry", "exit_time",
                        "pnl", "r", "delay_min", "hold_min", "tp_half",
                        "trail_activated", "trail_exit_pct", "giveback_pct",
                        "funding", "ambiguous", "legs"])
            for t in sorted(trades, key=lambda x: x["entry_ts"]):
                w.writerow([t["symbol"], t["side"], ts_str(t["entry_ts"]),
                            round(t["entry"], 2), ts_str(t["exit_ts"]),
                            round(t["pnl"], 2), round(t["r"], 2),
                            round(t["delay_min"], 0), round(t["hold_min"], 0),
                            t["tp_half"], t["trail_activated"],
                            t["trail_exit_pct"], t["giveback_pct"],
                            round(t["funding"], 3), t["ambiguous"],
                            "; ".join(f"{r}@{round(p,2)}" for _, p, r, _ in
                                      [(l[0], l[1], l[2], l[3]) for l in t["legs"]])])

    lines = [
        "# Entry-confirmation backtest report",
        f"\nWindow: {ts_str(start_ms)} → {ts_str(end_ms)} UTC · "
        f"Symbols: {', '.join(SYMBOLS)} · notional $200/trade of $1,000 allocated",
        "Costs: taker 0.055%/side + slippage 0.02%/side + historical funding. "
        "Exits on 1-minute data (conservative 15m fallback tagged).",
        "\n| metric | " + " | ".join(m.replace('mode', 'M') for m in MODES) + " |",
        "|---|" + "---|" * len(MODES),
    ]
    keys = [("trades", "trades"), ("win_rate", "win rate %"), ("net", "net $"),
            ("profit_factor", "profit factor"), ("expectancy_R", "expectancy (R)"),
            ("max_dd", "max drawdown $"), ("max_consec_losses", "max consec losses"),
            ("avg_delay_min", "avg entry delay (min)"),
            ("buy_pnl", "BUY pnl $"), ("sell_pnl", "SELL pnl $"),
            ("funding", "funding $"), ("ambiguous", "intrabar-ambiguous"),
            ("trail_activated_n", "trail halves activated"),
            ("trail_avg_exit_pct", "trail avg exit %"),
            ("trail_avg_giveback_pct", "trail avg give-back %")]
    for key, label in keys:
        row = [str(results[m].get(key, "—")) for m in MODES]
        lines.append(f"| {label} | " + " | ".join(row) + " |")
    lines.append("\n## Per-symbol net $")
    for m in MODES:
        lines.append(f"- **{m}**: {results[m].get('by_symbol', {})}")
    lines.append("\n## Missed / disarmed setups (counts by reason)")
    for m in MODES:
        lines.append(f"- **{m}**: {results[m].get('missed', {})}")
    lines.append(
        "\n## Disclaimers\n"
        "- Mode 1 is simulated on closed hourly candles (intended design); the "
        "live engine currently includes the forming candle — a known deviation, "
        "not fixed here by owner instruction.\n"
        "- These are evaluation results, **not a guarantee of future profit**. "
        "Win rate is not the primary metric — expectancy (R) and profit factor "
        "matter more.\n"
        "- Funding applied from Bybit's historical funding rates at each 8h "
        "timestamp a simulated position spanned.\n")
    with open(os.path.join(OUT_DIR, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    args = ap.parse_args()
    end_ms = (int(time.time() * 1000) // H1) * H1
    start_ms = end_ms - args.months * 30 * DAY

    print(f"fetching data {ts_str(start_ms)} → {ts_str(end_ms)} ...")
    markets = {s: Market(s, start_ms, end_ms) for s in SYMBOLS}
    store = data.OneMinuteStore()

    results, all_trades = {}, {}
    for mode in MODES:
        t0 = time.time()
        trades, missed = run_mode(mode, markets, start_ms, end_ms, store)
        results[mode] = summarize(mode, trades, missed)
        all_trades[mode] = trades
        print(f"{mode}: {len(trades)} trades  ({time.time()-t0:.0f}s)")
    write_outputs(results, all_trades, start_ms, end_ms)


if __name__ == "__main__":
    main()
