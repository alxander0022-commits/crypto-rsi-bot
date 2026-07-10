"""
RSI + Trend alert bot  (paper / alert-only — NEVER places real trades).

Watches several coins at once (config.SYMBOLS). Run once per hour from a
scheduler. Each run sends ONE Telegram message — one sentence per coin with
its trend, RSI, and a plain verdict:
  UPTREND   + RSI < RSI_BUY   -> 🟢 time to BUY
  DOWNTREND + RSI > RSI_SELL  -> 🔴 time to SELL
  otherwise                   -> ⛔ stay out
Every check is also logged to the CSV ledger. A separate 12-hour macro/news
report runs via `bot.py news`.

Usage:
  python bot.py                     # "run" — the hourly check of all coins
  python bot.py run
  python bot.py test                # send a test Telegram message
  python bot.py news                # 12h macro/political research (Claude API)
  python bot.py snapshot            # ping current reading for all coins now
  python bot.py buy BTCUSDT [price] # mark you bought a coin (omit price = current)
  python bot.py sell SOLUSDT        # mark you sold a coin
  python bot.py status              # show all positions
"""

import sys
from datetime import datetime, timezone

# Windows consoles default to cp1252, which can't encode the emoji in our
# messages. Force UTF-8 so printing/logging never crashes a scheduled run.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import config
import data
import indicators
import notify
import ledger
import state as state_mod


TREND_DISP = {"UPTREND": "UP", "DOWNTREND": "DOWN", "SIDEWAYS": "SIDE"}


# ─── helpers ──────────────────────────────────────────────────────────
def fmt(x, decimals=2):
    return f"{x:,.{decimals}f}"


def now_utc():
    return datetime.now(timezone.utc)


def pct(x):
    return int(round(x * 100))


def current_price(symbol):
    df = data.get_klines(symbol, config.ENTRY_TIMEFRAME, config.CATEGORY, limit=2)
    return float(df["close"].iloc[-1])


def resolve_symbol(token):
    """Turn user input like 'sol' or 'btc' into a full Bybit symbol."""
    t = token.upper()
    if t in config.SYMBOLS:
        return t
    if (t + "USDT") in config.SYMBOLS:
        return t + "USDT"
    return t if t.endswith("USDT") else (t + "USDT")


def fng_line():
    """Compact Fear & Greed line for the message, or None if unavailable.
    Never lets a Fear & Greed hiccup break the coin report."""
    try:
        import news
        return news.compact_fng()
    except Exception:
        return None


def read_market(symbol):
    """Fetch + compute for one symbol.
    Returns (price, rsi_prev, rsi, direction, adx)."""
    df_1h = data.get_klines(symbol, config.ENTRY_TIMEFRAME, config.CATEGORY, config.KLINE_LIMIT)
    df_trend = data.get_klines(symbol, config.TREND_TIMEFRAME, config.CATEGORY, config.KLINE_LIMIT)
    price = float(df_1h["close"].iloc[-1])
    rsi_prev, rsi = indicators.latest_rsi_pair(df_1h)
    direction, adx, _, _, _ = indicators.trend_state(df_trend)
    return price, rsi_prev, rsi, direction, adx


def decide_signal(direction, rsi_prev, rsi):
    """BUY / SELL / NONE for one coin.

    With CONFIRM_REVERSAL, only fire when RSI has crossed BACK through the level
    (the extreme is rolling over):
      UPTREND   + RSI crosses up   through RSI_BUY   -> BUY
      DOWNTREND + RSI crosses down through RSI_SELL  -> SELL
    Otherwise use the simple "RSI is beyond the level" trigger.
    """
    if config.CONFIRM_REVERSAL:
        if direction == "UPTREND" and rsi_prev < config.RSI_BUY <= rsi:
            return "BUY"
        if direction == "DOWNTREND" and rsi_prev > config.RSI_SELL >= rsi:
            return "SELL"
        return "NONE"
    # simple level-based
    if direction == "UPTREND" and rsi < config.RSI_BUY:
        return "BUY"
    if direction == "DOWNTREND" and rsi > config.RSI_SELL:
        return "SELL"
    return "NONE"


def setup_note(direction, rsi, signal):
    """A '⚠️ setup building' heads-up when a coin is within SETUP_WARN of the
    trigger level (in the right trend) but hasn't fired yet."""
    if signal != "NONE":
        return ""
    if direction == "DOWNTREND" and rsi >= config.RSI_SELL - config.SETUP_WARN:
        return " ⚠️ SELL setup building"
    if direction == "UPTREND" and rsi <= config.RSI_BUY + config.SETUP_WARN:
        return " ⚠️ BUY setup building"
    return ""


# ─── position actions ─────────────────────────────────────────────────
def do_buy(state, symbol, price):
    pos = state_mod.get_pos(state, symbol)
    pos["holding"] = True
    pos["entry_price"] = float(price)
    pos["entry_time"] = now_utc().isoformat()
    pos["last_signal"] = None
    target = price * (1 + config.TARGET_PCT)
    notify.send(
        f"{symbol} | POSITION OPENED\n"
        f"entry ${fmt(price)} | +{pct(config.TARGET_PCT)}% target ${fmt(target)}"
    )
    print(f"marked HOLDING {symbol} @ {price}")


def do_sell(state, symbol):
    pos = state_mod.get_pos(state, symbol)
    entry = pos.get("entry_price")
    pos["holding"] = False
    pos["entry_price"] = None
    pos["entry_time"] = None
    pos["last_signal"] = None
    note = f" (entry was ${fmt(entry)})" if entry else ""
    notify.send(f"{symbol} | POSITION CLOSED{note}")
    print(f"marked FLAT {symbol}")


def status_text(state):
    lines = [f"Watching: {', '.join(config.SYMBOLS)}"]
    # show configured coins first, then any other held coin
    seen = set()
    for symbol in list(config.SYMBOLS) + list(state.get("positions", {}).keys()):
        if symbol in seen:
            continue
        seen.add(symbol)
        pos = state.get("positions", {}).get(symbol)
        if pos and pos.get("holding"):
            entry = pos["entry_price"]
            try:
                price = current_price(symbol)
                pnl = (price / entry - 1) * 100
                lines.append(
                    f"{symbol}: HOLDING entry ${fmt(entry)} | now ${fmt(price)} "
                    f"| {pnl:+.2f}% | target ${fmt(entry * (1 + config.TARGET_PCT))}"
                )
            except Exception:
                lines.append(f"{symbol}: HOLDING entry ${fmt(entry)}")
        elif symbol in config.SYMBOLS:
            lines.append(f"{symbol}: flat")
    lines.append(
        f"UP+RSI<{config.RSI_BUY} = 🟢BUY | DOWN+RSI>{config.RSI_SELL} = 🔴SELL "
        f"| trend TF {config.TREND_TIMEFRAME}"
    )
    return "\n".join(lines)


def apply_commands(state, cmds):
    for cmd, args in cmds:
        if cmd == "buy":
            if not args:
                notify.send("usage: /buy SYMBOL [price]  e.g. /buy BTCUSDT  or  /buy SOL 77")
                continue
            symbol = resolve_symbol(args[0])
            price = float(args[1]) if len(args) > 1 else current_price(symbol)
            do_buy(state, symbol, price)
        elif cmd == "sell":
            if not args:
                notify.send("usage: /sell SYMBOL  e.g. /sell SOLUSDT")
                continue
            do_sell(state, resolve_symbol(args[0]))
        elif cmd == "status":
            notify.send(status_text(state))
        elif cmd in ("help", "start"):
            notify.send(
                "Commands:\n"
                "/buy SYMBOL [price] — mark bought (omit price = current)\n"
                "/sell SYMBOL — mark sold\n"
                "/status — show all positions\n"
                f"Watching: {', '.join(config.SYMBOLS)}"
            )


# ─── per-coin evaluation (the hourly check) ───────────────────────────
TREND_WORD = {"UPTREND": "UP", "DOWNTREND": "DOWN", "SIDEWAYS": "SIDEWAYS"}
VERDICT = {"BUY": "🟢 time to BUY", "SELL": "🔴 time to SELL", "NONE": "⛔ stay out"}
ACTION_WORD = {"BUY": "buy", "SELL": "sell", "NONE": "stay_out"}  # plain, for the CSV


def evaluate_symbol(state, symbol):
    """Compute one coin's trend + RSI + verdict, log a ledger row, and return
    the one-line sentence for the hourly message."""
    pos = state_mod.get_pos(state, symbol)
    price, rsi_prev, rsi, direction, adx = read_market(symbol)
    signal = decide_signal(direction, rsi_prev, rsi)

    pos["last_signal"] = signal
    ts = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    ledger.append([ts, symbol, round(price, 2), round(rsi, 1),
                   direction, round(adx, 1), signal, ACTION_WORD[signal]])

    short = symbol.replace("USDT", "")
    note = setup_note(direction, rsi, signal)
    return {
        "sentence": f"{short} — trend {TREND_WORD[direction]}, RSI {rsi:.0f} → {VERDICT[signal]}{note}",
        "console": f"{symbol} ${fmt(price)} | RSI {rsi:.1f} | {direction} "
                   f"(ADX {adx:.1f}) | {signal}{note}",
    }


def cmd_run():
    """One message, one sentence per coin: trend, RSI, and buy/sell/stay-out."""
    state = state_mod.load()

    # act on any Telegram commands sent since last run
    try:
        apply_commands(state, notify.poll_commands(state))
    except Exception as e:
        print(f"[command polling error] {e}")

    ts = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    sentences = []
    ok_count = 0
    for symbol in config.SYMBOLS:
        try:
            res = evaluate_symbol(state, symbol)
            print(f"{ts} | {res['console']}")
            sentences.append(res["sentence"])
            ok_count += 1
        except Exception as e:
            print(f"{ts} | [{symbol} error] {e}")
            sentences.append(f"{symbol.replace('USDT', '')} — data error")

    # one combined message (skip only if every coin failed, e.g. network down)
    if ok_count:
        fg = fng_line()
        if fg:
            sentences.append(fg)
        notify.send("\n".join(sentences))

    state_mod.save(state)


def cmd_snapshot():
    """Send the same one-sentence-per-coin message on demand (does not log)."""
    lines = []
    for symbol in config.SYMBOLS:
        try:
            price, rsi_prev, rsi, direction, adx = read_market(symbol)
            signal = decide_signal(direction, rsi_prev, rsi)
            short = symbol.replace("USDT", "")
            note = setup_note(direction, rsi, signal)
            lines.append(f"{short} — trend {TREND_WORD[direction]}, RSI {rsi:.0f} → {VERDICT[signal]}{note}")
        except Exception as e:
            lines.append(f"{symbol.replace('USDT', '')} — data error")
    fg = fng_line()
    if fg:
        lines.append(fg)
    msg = "\n".join(lines)
    ok = notify.send(msg)
    print(msg)
    print("(sent)" if ok else "(not sent — check creds)")


def cmd_test():
    ok = notify.send(
        "✅ Test alert from your RSI + Trend bot.\n"
        f"Watching: {', '.join(config.SYMBOLS)}\n"
        "If you can read this, alerts reach your phone."
    )
    print("test message sent" if ok else "not sent — check TELEGRAM_TOKEN / CHAT_ID")


# ─── entry point ──────────────────────────────────────────────────────
def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "run"

    if cmd == "run":
        cmd_run()
    elif cmd == "test":
        cmd_test()
    elif cmd == "news":
        import news
        news.run()
    elif cmd == "snapshot":
        cmd_snapshot()
    elif cmd == "buy":
        if len(sys.argv) < 3:
            print("usage: python bot.py buy SYMBOL [price]  e.g. python bot.py buy BTCUSDT 61000")
            return
        state = state_mod.load()
        symbol = resolve_symbol(sys.argv[2])
        price = float(sys.argv[3]) if len(sys.argv) > 3 else current_price(symbol)
        do_buy(state, symbol, price)
        state_mod.save(state)
    elif cmd == "sell":
        if len(sys.argv) < 3:
            print("usage: python bot.py sell SYMBOL  e.g. python bot.py sell SOLUSDT")
            return
        state = state_mod.load()
        do_sell(state, resolve_symbol(sys.argv[2]))
        state_mod.save(state)
    elif cmd == "status":
        print(status_text(state_mod.load()))
    else:
        print("usage: python bot.py [run|test|news|snapshot|buy SYMBOL [price]|sell SYMBOL|status]")


if __name__ == "__main__":
    main()
