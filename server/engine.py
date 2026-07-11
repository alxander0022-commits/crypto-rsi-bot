"""
The trading engine. Runs in a background thread:

  - every hour (at :01): compute signals (same trend+RSI-crossover strategy as
    the alert bot) and open positions — 1x, market order, with an
    exchange-native stop-loss attached and an exchange-native trailing stop
    (activation = arm %, distance = trail %). Exits live ON BYBIT, so a dead
    server can never leave an unprotected position.
  - every 5 minutes: reconcile — detect positions Bybit closed (stop/trailing),
    record them as trades, Telegram the result, enforce the daily-loss
    kill switch, and refresh the status snapshot the panel reads.

Safety gates before ANY order: paused flag, live-confirmation (in live mode),
symbol enabled, max open positions, daily loss limit, allocated-capital sizing.
"""

import os
import sys
import time
import traceback
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import indicators          # reused from the alert bot (repo root)
import notify              # reused Telegram sender (reads env vars)

from server import store
from server.bybit_client import Bybit, BybitError

TREND_TF = "D"
ENTRY_TF = "60"
KLINES = 400


def _now():
    return datetime.now(timezone.utc)


def _df(rows):
    """Bybit kline rows (newest first) -> DataFrame oldest-first, floats."""
    df = pd.DataFrame(rows, columns=["start", "open", "high", "low", "close",
                                     "volume", "turnover"])
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    return df.iloc[::-1].reset_index(drop=True)


def decide_signal(direction, rsi_prev, rsi, rsi_buy, rsi_sell):
    """Reversal-crossover strategy (same as the alert bot)."""
    if direction == "UPTREND" and rsi_prev < rsi_buy <= rsi:
        return "BUY"
    if direction == "DOWNTREND" and rsi_prev > rsi_sell >= rsi:
        return "SELL"
    return "NONE"


class Engine:
    def __init__(self):
        self.bybit = Bybit()
        self.last_hourly = None     # "YYYY-MM-DD HH" of the last hourly check
        self.last_error = None

    # ── market read ───────────────────────────────────────────────────
    def read_symbol(self, symbol):
        df1h = _df(self.bybit.klines(symbol, ENTRY_TF, KLINES))
        dfd = _df(self.bybit.klines(symbol, TREND_TF, KLINES))
        rsi_prev, rsi = indicators.latest_rsi_pair(df1h)
        direction, adx, _, _, _ = indicators.trend_state(dfd)
        price = float(df1h["close"].iloc[-1])
        return price, rsi_prev, rsi, direction, adx

    # ── safety ────────────────────────────────────────────────────────
    def can_trade(self, s, positions):
        if s["paused"]:
            return "paused"
        if self.bybit.mode == "live" and not s["live_confirmed"]:
            return "live mode not confirmed (GO-LIVE required)"
        if s.get("one_at_a_time") and len(positions) >= 1:
            return "one-at-a-time: a trade is already open"
        if len(positions) >= int(s["max_positions"]):
            return f"max positions ({s['max_positions']}) reached"
        loss_limit = float(s["max_daily_loss_pct"]) * float(s["allocated_capital"])
        if store.pnl_today() <= -loss_limit:
            return "daily loss limit hit"
        return None

    def check_daily_loss(self, s):
        loss_limit = float(s["max_daily_loss_pct"]) * float(s["allocated_capital"])
        if loss_limit > 0 and store.pnl_today() <= -loss_limit and not s["paused"]:
            store.save_settings({"paused": True})
            notify.send(f"🛑 KILL SWITCH: daily loss limit reached "
                        f"(-${loss_limit:,.0f}). Trading paused — resume from the panel.")

    # ── entries ───────────────────────────────────────────────────────
    def open_trade(self, s, symbol, signal, price):
        side = "Buy" if signal == "BUY" else "Sell"
        lev = max(1, min(10, int(s.get("leverage", 1))))
        # classic leverage: multiplies position size (owner decision 2026-07-11)
        notional = float(s["allocated_capital"]) * float(s["trade_fraction"]) * lev
        qty = self.bybit.round_qty(symbol, notional / price)
        if not qty:
            notify.send(f"⚠️ {symbol}: trade size ${notional:,.0f} is below the "
                        f"exchange minimum — skipped.")
            return

        exit_mode = s.get("exit_mode", "split")
        stop_pct = float(s["stop_pct"])
        tp_pct = float(s["take_profit_pct"])
        if side == "Buy":
            sl = self.bybit.round_price(symbol, price * (1 - stop_pct))
            tp = self.bybit.round_price(symbol, price * (1 + tp_pct))
        else:
            sl = self.bybit.round_price(symbol, price * (1 + stop_pct))
            tp = self.bybit.round_price(symbol, price * (1 - tp_pct))

        # apply the selected leverage and confirm it took effect on the position
        self.bybit.set_leverage(symbol, str(lev))
        self.bybit.market_order(symbol, side, qty, stop_loss=sl)  # SL always on exchange
        desc = [f"stop ${sl}"]
        try:
            pos_now = {p["symbol"]: p for p in self.bybit.positions()}.get(symbol)
            actual_lev = pos_now.get("leverage") if pos_now else None
            desc.append(f"leverage {actual_lev or lev}x"
                        + ("" if str(actual_lev or lev).startswith(str(lev)) else
                           f" ⚠️ (requested {lev}x)"))
        except Exception:
            desc.append(f"leverage {lev}x (unverified)")

        # trailing stop on exchange (trailing & split modes)
        if exit_mode in ("trailing", "split"):
            arm_pct = float(s["trail_activate_pct"])
            trail_pct = float(s["trail_distance_pct"])
            active = self.bybit.round_price(symbol,
                     price * (1 + arm_pct) if side == "Buy" else price * (1 - arm_pct))
            trail_dist = self.bybit.round_price(symbol, price * trail_pct)
            try:
                self.bybit.set_trailing(symbol, trail_dist, active)
                desc.append(f"trailing arms @ ${active}")
            except BybitError as e:
                desc.append(f"trailing failed ({e})")

        # take-profit
        if exit_mode == "take_profit":
            try:
                self.bybit.set_take_profit(symbol, tp)
                desc.append(f"take-profit ${tp}")
            except BybitError as e:
                desc.append(f"TP failed ({e})")
        elif exit_mode == "split":
            # bot closes HALF at the fixed target; the rest rides the trailing
            half = self.bybit.round_qty(symbol, float(qty) / 2)
            meta = store.get_runtime("split_meta", {})
            meta[symbol] = {"side": side, "tp": float(tp), "half_qty": half}
            store.set_runtime("split_meta", meta)
            desc.append(f"half take-profit @ ${tp}, half trails")

        m = self.bybit.mode.upper()
        notify.send(f"{'📈' if side == 'Buy' else '📉'} {m} {side.upper()} "
                    f"{symbol} qty {qty} @ ~${price:,.2f}\n" + " | ".join(desc))

    def hourly_check(self):
        s = store.get_settings()
        positions = {p["symbol"]: p for p in self.bybit.positions()}
        lines = []
        for symbol in s["symbols"]:
            try:
                price, rsi_prev, rsi, direction, adx = self.read_symbol(symbol)
                signal = decide_signal(direction, rsi_prev, rsi,
                                       float(s["rsi_buy"]), float(s["rsi_sell"]))
                lines.append({"symbol": symbol, "price": price, "rsi": round(rsi, 1),
                              "trend": direction, "adx": round(adx, 1), "signal": signal})

                pos = positions.get(symbol)
                # opposite signal closes an open position
                if pos and signal != "NONE":
                    held = pos["side"]  # Buy / Sell
                    if (signal == "SELL" and held == "Buy") or (signal == "BUY" and held == "Sell"):
                        self.bybit.close_position(symbol, held, pos["size"])
                        meta = store.get_runtime("split_meta", {})
                        meta.pop(symbol, None)
                        store.set_runtime("split_meta", meta)
                        notify.send(f"🔁 {symbol}: opposite signal — position closed.")
                        positions.pop(symbol, None)
                        pos = None

                if signal in ("BUY", "SELL") and not pos:
                    block = self.can_trade(s, positions)
                    if block:
                        notify.send(f"⚠️ {symbol} {signal} signal skipped: {block}")
                    else:
                        self.open_trade(s, symbol, signal, price)
                        positions[symbol] = {"symbol": symbol}  # count it
            except Exception as e:
                lines.append({"symbol": symbol, "error": str(e)})
        store.set_runtime("signals", lines)
        store.set_runtime("last_hourly", _now().strftime("%Y-%m-%d %H:%M UTC"))

    def split_take_half(self, positions):
        """Split mode: once price reaches the fixed target, market-close HALF
        (reduce-only); the remaining half keeps riding the exchange trailing."""
        meta = store.get_runtime("split_meta", {})
        if not meta:
            return
        by_sym = {p["symbol"]: p for p in positions}
        for symbol, m in list(meta.items()):
            pos = by_sym.get(symbol)
            if not pos:                       # position fully gone already
                meta.pop(symbol, None); continue
            price = float(pos.get("markPrice") or 0)
            hit = (price >= m["tp"]) if m["side"] == "Buy" else (price <= m["tp"])
            if hit:
                half = m.get("half_qty")
                if half and float(pos["size"]) > float(half):
                    try:
                        self.bybit.close_position(symbol, m["side"], half)
                        notify.send(f"🎯 {symbol}: half booked at target ${m['tp']} — "
                                    f"the rest rides the trailing stop.")
                    except Exception as e:
                        print(f"[split half-close {symbol}] {e}")
                meta.pop(symbol, None)
        store.set_runtime("split_meta", meta)

    # ── reconcile / sync ──────────────────────────────────────────────
    def sync(self):
        s = store.get_settings()
        positions = self.bybit.positions()
        try:
            self.split_take_half(positions)
            positions = self.bybit.positions()  # refresh after any half-close
        except Exception as e:
            print(f"[split] {e}")

        # Sweep Bybit's closed-PnL history (last 24h) every cycle and record
        # anything new — dedup by exchange order id, so restarts, crashes and
        # exchange-side closes (stop/trailing/manual) can never lose a trade.
        start = int(time.time() * 1000) - 24 * 3600 * 1000
        try:
            closed = self.bybit.closed_pnl(start_ms=start)
        except Exception:
            closed = []
        for rec in closed:
            ts_ms = int(rec.get("updatedTime") or rec.get("createdTime") or 0)
            closed_at = (datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                         .strftime("%Y-%m-%d %H:%M:%S") if ts_ms
                         else _now().strftime("%Y-%m-%d %H:%M:%S"))
            is_new = store.record_trade(
                closed_at=closed_at,
                symbol=rec["symbol"], side=rec["side"],
                qty=float(rec.get("qty") or 0),
                entry=float(rec.get("avgEntryPrice") or 0),
                exit_price=float(rec.get("avgExitPrice") or 0),
                pnl=float(rec.get("closedPnl") or 0),
                exchange_id=rec.get("orderId"),
                leverage=float(rec.get("leverage") or 0) or None,
            )
            if is_new:
                pnl = float(rec.get("closedPnl") or 0)
                icon = "✅" if pnl >= 0 else "❌"
                notify.send(f"{icon} {rec['symbol']} closed: "
                            f"{'+' if pnl >= 0 else ''}{pnl:,.2f} USDT "
                            f"(entry {rec.get('avgEntryPrice')} → {rec.get('avgExitPrice')})")

        store.set_runtime("open_symbols", sorted(p["symbol"] for p in positions))

        # status snapshot for the panel
        try:
            equity, avail = self.bybit.wallet_usdt()
        except Exception:
            equity = avail = None
        store.set_runtime("status", {
            "time": _now().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "mode": self.bybit.mode,
            "equity": equity, "available": avail,
            "positions": [{
                "symbol": p["symbol"], "side": p["side"], "size": p["size"],
                "entry": p.get("avgPrice"), "mark": p.get("markPrice"),
                "upnl": p.get("unrealisedPnl"), "sl": p.get("stopLoss"),
                "trailing": p.get("trailingStop"), "lev": p.get("leverage"),
            } for p in positions],
            "pnl_today": round(store.pnl_today(), 2),
            "last_error": self.last_error,
        })
        self.check_daily_loss(s)

    # ── main loop ─────────────────────────────────────────────────────
    def loop(self):
        notify.send(f"🤖 Trading engine started ({self.bybit.mode.upper()} mode).")
        last_sync = 0
        while True:
            try:
                now = _now()
                hour_key = now.strftime("%Y-%m-%d %H")
                if now.minute >= 1 and self.last_hourly != hour_key:
                    self.last_hourly = hour_key
                    self.hourly_check()
                if time.time() - last_sync >= 300:
                    last_sync = time.time()
                    self.sync()
                self.last_error = None
            except Exception as e:
                self.last_error = f"{_now():%H:%M} {e}"
                traceback.print_exc()
            time.sleep(20)

    # ── panel actions ─────────────────────────────────────────────────
    def stop_all(self):
        """Market-close every open position and pause the engine."""
        closed = []
        for p in self.bybit.positions():
            self.bybit.close_position(p["symbol"], p["side"], p["size"])
            closed.append(p["symbol"])
        store.set_runtime("split_meta", {})
        store.save_settings({"paused": True})
        notify.send(f"🛑 STOP ALL: closed {', '.join(closed) if closed else 'nothing open'}; "
                    f"trading paused.")
        return closed
