"""
Telegram sending + lightweight command polling.

Sending: POST to the Bot API sendMessage endpoint.
Polling: on each hourly run we read new messages via getUpdates so you can
reply "/buy 61000" or "/sell" from your phone to tell the bot what you did.
"""

import requests

import config

API = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"


def creds_ok():
    """True if the token/chat id look filled in (not the placeholder text)."""
    return (
        "PASTE" not in str(config.TELEGRAM_TOKEN)
        and "PASTE" not in str(config.TELEGRAM_CHAT_ID)
        and str(config.TELEGRAM_TOKEN).strip() != ""
    )


def send(text):
    """Send a message. If creds aren't configured, print to console instead."""
    if not creds_ok():
        print("[telegram not configured — would have sent]\n" + text + "\n")
        return False
    try:
        r = requests.post(
            f"{API}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
            timeout=20,
        )
        r.raise_for_status()
        return True
    except Exception as e:  # network / auth issues shouldn't crash the run
        print(f"[telegram send failed] {e}")
        return False


def send_photo(photo_url, caption=""):
    """Send an image by URL (Telegram fetches it server-side)."""
    if not creds_ok():
        print(f"[telegram not configured — would send photo] {photo_url}")
        return False
    try:
        r = requests.post(
            f"{API}/sendPhoto",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "photo": photo_url, "caption": caption},
            timeout=30,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[telegram photo failed] {e}")
        return False


def poll_commands(state):
    """
    Read new Telegram messages addressed to the configured chat and return a
    list of (command, args) tuples where args is the list of remaining tokens,
    e.g. "/buy BTCUSDT 61000" -> ("buy", ["BTCUSDT", "61000"]).

    Mutates state["last_update_id"] so the same message isn't processed twice.
    """
    if not creds_ok():
        return []

    try:
        offset = int(state.get("last_update_id", 0)) + 1
        r = requests.get(
            f"{API}/getUpdates",
            params={"offset": offset, "timeout": 0},
            timeout=20,
        )
        r.raise_for_status()
        updates = r.json().get("result", [])
    except Exception as e:
        print(f"[telegram poll failed] {e}")
        return []

    handled = []
    for u in updates:
        state["last_update_id"] = u["update_id"]
        msg = u.get("message") or u.get("channel_post")
        if not msg:
            continue
        chat_id = str(msg.get("chat", {}).get("id"))
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            continue  # ignore messages from anyone else
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        parts = text.split()
        cmd = parts[0].lower().lstrip("/")
        args = parts[1:]
        handled.append((cmd, args))

    return handled
