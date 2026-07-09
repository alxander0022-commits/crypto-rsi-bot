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


def load():
    if not os.path.exists(config.STATE_FILE):
        return {"positions": {}, "last_update_id": 0}
    with open(config.STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    # migrate any older single-symbol file: just keep the update id
    if "positions" not in state:
        state = {"positions": {}, "last_update_id": state.get("last_update_id", 0)}
    state.setdefault("positions", {})
    state.setdefault("last_update_id", 0)
    return state


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
