"""
12-hour market-mood report.

Sends, every 12 hours:
  1. The crypto Fear & Greed Index (free public API) as a text gauge, plus the
     official gauge chart image.
  2. A short read on market sentiment (are people buying / selling / panicking?),
     the political / economic direction, and a BULLISH / BEARISH / NEUTRAL call
     per coin — researched live via the Claude API with web search.

The Fear & Greed part needs NO API key and works immediately. The macro
commentary needs an Anthropic API key (config.ANTHROPIC_API_KEY or the
ANTHROPIC_API_KEY env var). Rough cost: a few cents to ~$0.30 per run.

Run via:  python bot.py news
"""

from datetime import datetime, timezone

import requests

import config
import notify

# Free, no-key Fear & Greed Index (0 = extreme fear, 100 = extreme greed)
FNG_API = "https://api.alternative.me/fng/?limit=2&format=json"
# Official gauge + history chart image (updates daily)
FNG_CHART_URL = "https://alternative.me/crypto/fear-and-greed-index.png"

FNG_EMOJI = {
    "Extreme Fear": "😱",
    "Fear": "😨",
    "Neutral": "😐",
    "Greed": "😀",
    "Extreme Greed": "🤑",
}

SYSTEM = (
    "You are a crypto market analyst. Be concise, factual and neutral — no hype, "
    "no financial advice. Base claims on what you find via search."
)


# ─── Fear & Greed ─────────────────────────────────────────────────────
def fetch_fng():
    """Return {value, label, yesterday} or None on failure."""
    r = requests.get(FNG_API, timeout=20)
    r.raise_for_status()
    data = r.json()["data"]
    today = data[0]
    y = data[1] if len(data) > 1 else None
    return {
        "value": int(today["value"]),
        "label": today["value_classification"],
        "yesterday": int(y["value"]) if y else None,
    }


def compact_fng():
    """One compact line for the hourly message, e.g.
    'Fear & Greed: 20/100 — Extreme Fear 😱'. Returns None on failure."""
    try:
        fng = fetch_fng()
    except Exception:
        return None
    return f"Fear & Greed: {fng['value']}/100 — {fng['label']} {FNG_EMOJI.get(fng['label'], '')}".rstrip()


def fng_gauge(fng):
    """Text gauge like:  Fear & Greed: 27/100 — Fear 😨  (yesterday 31 ▼)
    [█████░░░░░░░░░░░░░░░░]"""
    value, label = fng["value"], fng["label"]
    filled = max(0, min(20, round(value / 5)))
    bar = "█" * filled + "░" * (20 - filled)
    emoji = FNG_EMOJI.get(label, "")
    trend = ""
    if fng["yesterday"] is not None:
        d = value - fng["yesterday"]
        arrow = "▲" if d > 0 else ("▼" if d < 0 else "▬")
        trend = f"  (yesterday {fng['yesterday']} {arrow})"
    return f"Fear & Greed: {value}/100 — {label} {emoji}{trend}\n[{bar}]"


# ─── Claude macro / sentiment commentary ──────────────────────────────
def _coins_plain():
    return ", ".join(s.replace("USDT", "") for s in config.SYMBOLS)


def _prompt(fng):
    coins = _coins_plain()
    fng_line = ""
    if fng:
        fng_line = (
            f"The crypto Fear & Greed Index is currently {fng['value']}/100 "
            f"({fng['label']}). Factor that in.\n\n"
        )
    return (
        f"{fng_line}"
        f"Research the current crypto market mood and the political/economic "
        f"backdrop over roughly the last 12 hours (search the web). Cover: are "
        f"traders broadly buying, selling, or panicking? interest-rate / "
        f"central-bank news, US/EU regulation and SEC actions, geopolitics, "
        f"major ETF or exchange flows, and overall risk sentiment.\n\n"
        f"Write a SHORT Telegram message (plain text, no markdown headers, "
        f"max ~12 lines):\n"
        f"Line 1: 'Mood: <one sentence — are people buying/selling/panicking "
        f"and why>'\n"
        f"Then one line per coin ({coins}): 'BTC: BULLISH/BEARISH/NEUTRAL — "
        f"<3-8 word reason on whether its trend holds or could flip>'\n"
        f"Then 2-4 bullet headlines, each with a source name.\n"
        f"End with one line: overall risk tone (risk-on / risk-off / mixed)."
    )


def creds_ok():
    key = str(config.ANTHROPIC_API_KEY)
    return "PASTE" not in key and key.strip() != ""


def _macro_text(fng):
    try:
        import anthropic
    except ImportError:
        return "(macro commentary unavailable: run `pip install -r requirements.txt`)"

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    tools = [{"type": "web_search_20260209", "name": "web_search"}]
    messages = [{"role": "user", "content": _prompt(fng)}]

    try:
        resp = None
        for _ in range(6):  # web-search server loop may pause; re-send to continue
            resp = client.messages.create(
                model=config.NEWS_MODEL,
                max_tokens=2000,
                system=SYSTEM,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                tools=tools,
                messages=messages,
            )
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
    except Exception as e:
        return f"(macro commentary failed: {e})"

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text or "(macro commentary: no summary produced)"


# ─── entry point ──────────────────────────────────────────────────────
def run():
    if not config.NEWS_REPORT_ON:
        print("[news] NEWS_REPORT_ON is False — skipping.")
        return

    # 1) Fear & Greed (no key needed)
    fng = None
    try:
        fng = fetch_fng()
    except Exception as e:
        print(f"[news] Fear & Greed fetch failed: {e}")

    gauge = fng_gauge(fng) if fng else "Fear & Greed: unavailable right now."

    # 2) optional AI market commentary (off by default = free). Only runs if
    # NEWS_MACRO_ON is True *and* an Anthropic key is set.
    macro = ""
    if getattr(config, "NEWS_MACRO_ON", False) and creds_ok():
        macro = "\n\n" + _macro_text(fng)

    msg = f"🗞️ 12h market check\n{gauge}{macro}"

    notify.send(msg)
    # the official Fear & Greed gauge + history chart, as an image
    notify.send_photo(FNG_CHART_URL, caption="Crypto Fear & Greed — recent history")
    print(msg)

    # keep a local record
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with open(config.NEWS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n===== {ts} =====\n{msg}\n")
    except Exception as e:
        print(f"[news] could not write {config.NEWS_LOG_FILE}: {e}")
