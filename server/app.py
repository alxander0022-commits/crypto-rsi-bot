"""
Control-panel web app + engine host.

Run:  uvicorn server.app:app --host 0.0.0.0 --port 8080
(on the VPS this is behind Tailscale — not exposed to the public internet)

Auth: single password from PANEL_PASSWORD in server/.env. Session = random
token in an HttpOnly cookie. All /api/* routes (except /api/login) require it.
"""

import hmac
import os
import secrets
import threading

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))  # before engine import

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from server import store
from server.engine import Engine

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

_sessions = set()
_engine = None

NUMERIC_BOUNDS = {
    "allocated_capital": (10, 10_000_000),
    "trade_fraction": (0.01, 1.0),
    "stop_pct": (0.002, 0.5),
    "take_profit_pct": (0.005, 0.5),
    "trail_activate_pct": (0.005, 0.5),
    "trail_distance_pct": (0.005, 0.5),
    "rsi_buy": (5, 50),
    "rsi_sell": (50, 95),
    "max_positions": (1, 10),
    "max_daily_loss_pct": (0.005, 0.5),
}
EXIT_MODES = ("take_profit", "trailing", "split")


@app.on_event("startup")
def startup():
    global _engine
    store.init()
    _engine = Engine()
    threading.Thread(target=_engine.loop, daemon=True).start()


def _authed(request: Request):
    tok = request.cookies.get("session", "")
    return tok in _sessions


def _deny():
    return JSONResponse({"error": "not logged in"}, status_code=401)


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "panel.html"),
                        headers={"Cache-Control": "no-store, max-age=0"})


@app.post("/api/login")
async def login(request: Request, response: Response):
    body = await request.json()
    expected = os.getenv("PANEL_PASSWORD", "").strip()
    supplied = str(body.get("password", "")).strip()   # tolerate stray spaces
    if not expected or not hmac.compare_digest(supplied, expected):
        return JSONResponse({"error": "wrong password"}, status_code=401)
    tok = secrets.token_urlsafe(32)
    _sessions.add(tok)
    response.set_cookie("session", tok, httponly=True, samesite="strict",
                        max_age=30 * 24 * 3600)
    return {"ok": True}


@app.get("/api/status")
def status(request: Request):
    if not _authed(request):
        return _deny()
    s = store.get_settings()
    return {
        "mode": _engine.bybit.mode,
        "settings": s,
        "status": store.get_runtime("status", {}),
        "signals": store.get_runtime("signals", []),
        "last_hourly": store.get_runtime("last_hourly"),
        "trades": store.trades(30),
        "stats": store.stats(),
    }


@app.post("/api/settings")
async def save_settings(request: Request):
    if not _authed(request):
        return _deny()
    body = await request.json()
    updates = {}
    for key, (lo, hi) in NUMERIC_BOUNDS.items():
        if key in body:
            try:
                v = float(body[key])
            except (TypeError, ValueError):
                return JSONResponse({"error": f"{key} must be a number"}, status_code=400)
            if not (lo <= v <= hi):
                return JSONResponse({"error": f"{key} must be between {lo} and {hi}"},
                                    status_code=400)
            updates[key] = int(v) if key == "max_positions" else v
    if "symbols" in body:
        syms = [str(x).upper().strip() for x in body["symbols"] if str(x).strip()]
        if not syms:
            return JSONResponse({"error": "need at least one symbol"}, status_code=400)
        updates["symbols"] = syms
    if "exit_mode" in body:
        if body["exit_mode"] not in EXIT_MODES:
            return JSONResponse({"error": "exit_mode must be take_profit/trailing/split"},
                                status_code=400)
        updates["exit_mode"] = body["exit_mode"]
    if "one_at_a_time" in body:
        updates["one_at_a_time"] = bool(body["one_at_a_time"])
    if "paused" in body:
        updates["paused"] = bool(body["paused"])
    store.save_settings(updates)
    return {"ok": True, "saved": sorted(updates)}


@app.post("/api/stopall")
def stopall(request: Request):
    if not _authed(request):
        return _deny()
    closed = _engine.stop_all()
    return {"ok": True, "closed": closed}


@app.post("/api/golive")
async def golive(request: Request):
    if not _authed(request):
        return _deny()
    body = await request.json()
    if body.get("confirm") != "GO-LIVE":
        return JSONResponse({"error": 'type exactly "GO-LIVE" to confirm'}, status_code=400)
    store.save_settings({"live_confirmed": True})
    return {"ok": True}
