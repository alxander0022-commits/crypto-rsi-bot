"""
Minimal Bybit V5 REST client — demo & live.

MODE comes from the environment (BYBIT_MODE=demo|live, default demo):
  demo -> https://api-demo.bybit.com   (real API, fake funds)
  live -> https://api.bybit.com        (real money — only after GO-LIVE)

API keys come from the environment ONLY (BYBIT_API_KEY / BYBIT_API_SECRET,
normally via server/.env). They are never stored in code or git.

Docs: https://bybit-exchange.github.io/docs/v5/intro
"""

import hashlib
import hmac
import json
import os
import time
from decimal import Decimal, ROUND_DOWN

import requests

RECV_WINDOW = "15000"

BASES = {
    "demo": "https://api-demo.bybit.com",
    "live": "https://api.bybit.com",
}


class BybitError(RuntimeError):
    def __init__(self, code, msg, endpoint=""):
        super().__init__(f"Bybit {endpoint} retCode={code}: {msg}")
        self.code = code


class Bybit:
    def __init__(self, mode=None, api_key=None, api_secret=None):
        self.mode = (mode or os.getenv("BYBIT_MODE", "demo")).lower()
        if self.mode not in BASES:
            raise ValueError(f"BYBIT_MODE must be demo or live, got {self.mode!r}")
        self.base = BASES[self.mode]
        self.key = api_key or os.getenv("BYBIT_API_KEY", "")
        self.secret = api_secret or os.getenv("BYBIT_API_SECRET", "")
        self._instr_cache = {}

    # ── low level ─────────────────────────────────────────────────────
    def _sign(self, ts, payload):
        raw = f"{ts}{self.key}{RECV_WINDOW}{payload}"
        return hmac.new(self.secret.encode(), raw.encode(), hashlib.sha256).hexdigest()

    def _headers(self, payload):
        ts = str(int(time.time() * 1000))
        return {
            "X-BAPI-API-KEY": self.key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN": self._sign(ts, payload),
            "Content-Type": "application/json",
        }

    def _get(self, path, params=None, auth=False):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self.base}{path}" + (f"?{qs}" if qs else "")
        headers = self._headers(qs) if auth else {}
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            raise BybitError(data.get("retCode"), data.get("retMsg"), path)
        return data["result"]

    def _post(self, path, body):
        body = {k: v for k, v in body.items() if v is not None}
        payload = json.dumps(body)
        r = requests.post(f"{self.base}{path}", data=payload,
                          headers=self._headers(payload), timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            raise BybitError(data.get("retCode"), data.get("retMsg"), path)
        return data["result"]

    # ── public market data ────────────────────────────────────────────
    def klines(self, symbol, interval, limit=400):
        """OHLC rows newest-first: [start_ms, open, high, low, close, vol, turnover]"""
        res = self._get("/v5/market/kline", {
            "category": "linear", "symbol": symbol,
            "interval": interval, "limit": limit,
        })
        return res["list"]

    def instrument(self, symbol):
        """Cached lot/price filters: qty_step, min_qty, tick_size."""
        if symbol not in self._instr_cache:
            res = self._get("/v5/market/instruments-info",
                            {"category": "linear", "symbol": symbol})
            info = res["list"][0]
            self._instr_cache[symbol] = {
                "qty_step": info["lotSizeFilter"]["qtyStep"],
                "min_qty": info["lotSizeFilter"]["minOrderQty"],
                "tick_size": info["priceFilter"]["tickSize"],
            }
        return self._instr_cache[symbol]

    def last_price(self, symbol):
        res = self._get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
        return float(res["list"][0]["lastPrice"])

    # ── account (auth) ────────────────────────────────────────────────
    def wallet_usdt(self):
        """(equity, available) of the unified account in USDT terms."""
        res = self._get("/v5/account/wallet-balance",
                        {"accountType": "UNIFIED"}, auth=True)
        acct = res["list"][0]
        return float(acct.get("totalEquity") or 0), float(acct.get("totalAvailableBalance") or 0)

    def positions(self):
        """Open linear USDT positions (size > 0)."""
        res = self._get("/v5/position/list",
                        {"category": "linear", "settleCoin": "USDT"}, auth=True)
        return [p for p in res["list"] if float(p.get("size") or 0) > 0]

    def closed_pnl(self, symbol=None, start_ms=None, limit=50):
        res = self._get("/v5/position/closed-pnl", {
            "category": "linear", "symbol": symbol,
            "startTime": start_ms, "limit": limit,
        }, auth=True)
        return res["list"]

    # ── trading (auth) ────────────────────────────────────────────────
    def set_leverage(self, symbol, leverage="1"):
        try:
            self._post("/v5/position/set-leverage", {
                "category": "linear", "symbol": symbol,
                "buyLeverage": leverage, "sellLeverage": leverage,
            })
        except BybitError as e:
            if e.code != 110043:  # 110043 = leverage not modified (already set)
                raise

    def market_order(self, symbol, side, qty, stop_loss=None, reduce_only=False):
        """side: 'Buy'|'Sell'. qty/stop_loss as strings. positionIdx=0 (one-way)."""
        return self._post("/v5/order/create", {
            "category": "linear", "symbol": symbol, "side": side,
            "orderType": "Market", "qty": str(qty), "positionIdx": 0,
            "stopLoss": stop_loss, "reduceOnly": reduce_only or None,
        })

    def set_trailing(self, symbol, distance, active_price):
        """Exchange-native trailing stop: arms at active_price, then trails
        `distance` (absolute price) off the best price. Survives bot death."""
        return self._post("/v5/position/trading-stop", {
            "category": "linear", "symbol": symbol, "positionIdx": 0,
            "trailingStop": str(distance), "activePrice": str(active_price),
        })

    def close_position(self, symbol, side, size):
        """Market-close: opposite-side reduce-only order for the full size."""
        opposite = "Sell" if side == "Buy" else "Buy"
        return self.market_order(symbol, opposite, size, reduce_only=True)

    # ── helpers ───────────────────────────────────────────────────────
    def round_qty(self, symbol, qty):
        """Round qty DOWN to the instrument's step; '' if below the minimum."""
        instr = self.instrument(symbol)
        step = Decimal(instr["qty_step"])
        q = (Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_DOWN) * step
        if q < Decimal(instr["min_qty"]):
            return ""
        return format(q.normalize(), "f")

    def round_price(self, symbol, price):
        instr = self.instrument(symbol)
        tick = Decimal(instr["tick_size"])
        p = (Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        return format(p.normalize(), "f")
