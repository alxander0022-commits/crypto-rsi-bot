"""
Paper (simulated) auto-trader. NEVER touches an exchange — it maintains a
virtual account in state["portfolio"] and logs closed trades to trades.csv.

Each hour, for one coin, process():
  - manages an open simulated position (stop-loss / take-profit / opposite
    signal), closing it and booking P&L into paper equity, and
  - opens a new LONG on a 🟢 BUY signal or SHORT on a 🔴 SELL signal.

Returns a list of human-readable event strings for Telegram.
"""

import config
import ledger


def _f(x):
    return f"{x:,.2f}"


def _short(symbol):
    return symbol.replace("USDT", "")


def _open(pf, symbol, signal, price, ts):
    side = "long" if signal == "BUY" else "short"
    size_usd = pf["equity"] * config.TRADE_FRACTION
    qty = size_usd / price if price else 0.0
    if side == "long":
        stop = price * (1 - config.STOP_PCT)
        target = price * (1 + config.TARGET_PCT)
    else:
        stop = price * (1 + config.STOP_PCT)
        target = price * (1 - config.TARGET_PCT)
    pf["open"][symbol] = {
        "side": side, "entry": price, "qty": qty, "size_usd": size_usd,
        "stop": stop, "target": target, "opened": ts,
    }
    icon = "📈" if side == "long" else "📉"
    return (f"{icon} PAPER {side.upper()} {_short(symbol)} @ ${_f(price)}\n"
            f"stop ${_f(stop)} | target ${_f(target)} | size ${_f(size_usd)}")


def _close(pf, symbol, pos, exit_price, reason, ts):
    entry, qty, side = pos["entry"], pos["qty"], pos["side"]
    pnl = qty * (exit_price - entry) if side == "long" else qty * (entry - exit_price)
    pnl_pct = (pnl / (qty * entry) * 100) if qty and entry else 0.0
    pf["equity"] += pnl
    ledger.append_trade([pos["opened"], ts, symbol, side, round(entry, 2),
                         round(exit_price, 2), round(qty, 6), round(pnl, 2),
                         round(pnl_pct, 2), reason])
    del pf["open"][symbol]
    icon = "✅" if pnl >= 0 else "❌"
    reason_txt = {"stop": "stop-loss", "target": "target hit",
                  "flip": "opposite signal"}.get(reason, reason)
    sign = "+" if pnl >= 0 else "-"
    return (f"{icon} PAPER {_short(symbol)} {side} closed ({reason_txt})\n"
            f"{pnl_pct:+.2f}% | {sign}${_f(abs(pnl))} | equity ${_f(pf['equity'])}")


def _check_exit(pf, symbol, pos, price, hi, lo, signal, ts):
    """Return a close-event string if the position should exit, else None.
    Stop is checked before target (conservative on an ambiguous candle)."""
    side = pos["side"]
    exit_price = reason = None
    if side == "long":
        if lo <= pos["stop"]:
            exit_price, reason = pos["stop"], "stop"
        elif hi >= pos["target"]:
            exit_price, reason = pos["target"], "target"
        elif signal == "SELL":
            exit_price, reason = price, "flip"
    else:  # short
        if hi >= pos["stop"]:
            exit_price, reason = pos["stop"], "stop"
        elif lo <= pos["target"]:
            exit_price, reason = pos["target"], "target"
        elif signal == "BUY":
            exit_price, reason = price, "flip"
    if exit_price is None:
        return None
    return _close(pf, symbol, pos, exit_price, reason, ts)


def process(state, symbol, signal, price, hi, lo, ts):
    """Run the paper engine for one coin. Returns a list of event strings."""
    if not config.PAPER_TRADING:
        return []
    events = []
    pf = state["portfolio"]

    # 1) manage an existing open position
    if symbol in pf["open"]:
        ev = _check_exit(pf, symbol, pf["open"][symbol], price, hi, lo, signal, ts)
        if ev:
            events.append(ev)

    # 2) open a fresh position if flat and a signal fired this hour
    if symbol not in pf["open"] and signal in ("BUY", "SELL"):
        if signal == "SELL" and not config.ALLOW_SHORTS:
            return events
        events.append(_open(pf, symbol, signal, price, ts))

    return events


def unrealized(pos, price):
    """Live P&L (USD, %) of an open position at the given price."""
    entry, qty, side = pos["entry"], pos["qty"], pos["side"]
    pnl = qty * (price - entry) if side == "long" else qty * (entry - price)
    pct = (pnl / (qty * entry) * 100) if qty and entry else 0.0
    return pnl, pct
