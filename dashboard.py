"""
Writes docs/data.json each run — the data the static website (docs/index.html)
reads to show signals, Fear & Greed, paper positions, trade history and P&L.
"""

import json
import os
from datetime import datetime, timezone

import config
import ledger
import trader


def _stats(trades):
    n = len(trades)
    wins = sum(1 for t in trades if float(t["pnl_usd"]) >= 0)
    total = sum(float(t["pnl_usd"]) for t in trades)
    return {
        "n_trades": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(wins / n * 100, 1) if n else 0.0,
        "total_pnl": round(total, 2),
    }


def write(state, coins):
    pf = state["portfolio"]
    price_by = {c["symbol"]: c["price"] for c in coins}

    open_list = []
    for sym, pos in pf["open"].items():
        px = price_by.get(sym, pos["entry"])
        upnl, upct = trader.unrealized(pos, px)
        open_list.append({
            "symbol": sym, "short": sym.replace("USDT", ""), "side": pos["side"],
            "entry": round(pos["entry"], 2), "price": round(px, 2),
            "stop": round(pos["stop"], 2),
            "target": round(pos["target"], 2) if pos.get("target") is not None else None,
            "trail_active": pos.get("trail_active", False),
            "size_usd": round(pos.get("size_usd", 0), 2),
            "upnl": round(upnl, 2), "upnl_pct": round(upct, 2),
            "opened": pos["opened"],
        })

    trades = ledger.read_trades()
    equity = pf["equity"]
    equity_mtm = equity + sum(o["upnl"] for o in open_list)

    fng = None
    try:
        import news
        fng = news.fetch_fng()
    except Exception:
        pass

    data = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "coins": coins,
        "fng": ({"value": fng["value"], "label": fng["label"]} if fng else None),
        "portfolio": {
            "start": config.START_BALANCE,
            "equity": round(equity, 2),
            "equity_mtm": round(equity_mtm, 2),
            "return_pct": round((equity_mtm / config.START_BALANCE - 1) * 100, 2),
            "open": open_list,
        },
        "stats": _stats(trades),
        "trades": trades[-30:][::-1],  # newest first
        "settings": {
            "rsi_buy": config.RSI_BUY, "rsi_sell": config.RSI_SELL,
            "stop_pct": round(config.STOP_PCT * 100, 1),
            "target_pct": round(config.TARGET_PCT * 100, 1),
            "trade_fraction": round(config.TRADE_FRACTION * 100, 1),
        },
    }

    path = config.DATA_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
