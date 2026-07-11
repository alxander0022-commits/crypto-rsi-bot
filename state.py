"""
Per-symbol position store (JSON file). Each coin tracks its own holding,
entry price and last alert. One shared `last_update_id` for Telegram polling.

Shape:
{
  "positions": {
    "BTCUSDT": {"holding": false, "entry_price": null, "entry_time": null, "last_signal": null},
    ...
  },
  "last_update_id": 0
}
"""

import json
import os

import config


def _default_pos():
    return {"holding": False, "entry_price": None, "entry_time": None, "last_signal": None}


def _default_portfolio():
    # equity = realized paper account value; `open` holds live simulated trades
    return {"equity": config.START_BALANCE, "open": {}}


def load():
    if not os.path.exists(config.STATE_FILE):
        state = {"positions": {}, "last_update_id": 0}
    else:
        with open(config.STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    # migrate any older single-symbol file: just keep the update id
    if "positions" not in state:
        state = {"positions": {}, "last_update_id": state.get("last_update_id", 0)}
    state.setdefault("positions", {})
    state.setdefault("last_update_id", 0)
    state.setdefault("portfolio", _default_portfolio())
    state["portfolio"].setdefault("equity", config.START_BALANCE)
    state["portfolio"].setdefault("open", {})
    return state


def portfolio(state):
    return state["portfolio"]


def get_pos(state, symbol):
    """Return the position dict for a symbol, creating a default if absent."""
    pos = state["positions"].get(symbol)
    if pos is None:
        pos = _default_pos()
        state["positions"][symbol] = pos
    else:
        for key, val in _default_pos().items():
            pos.setdefault(key, val)
    return pos


def save(state):
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
