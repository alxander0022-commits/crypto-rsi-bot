#!/usr/bin/env bash
# VPS bootstrap (Ubuntu/Debian). Run as root:  bash deploy/setup.sh
# Installs the trading engine + control panel as a systemd service, and
# Tailscale so the panel is reachable only from your own devices.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/alxander0022-commits/crypto-rsi-bot.git}"
APP_DIR=/opt/trading

echo "== 0) Sanity: can this VPS reach Bybit? =="
code=$(curl -s -o /dev/null -w "%{http_code}" "https://api.bybit.com/v5/market/time" || true)
echo "api.bybit.com -> HTTP $code"
if [ "$code" != "200" ]; then
  echo "!! Bybit is NOT reachable from this VPS (HTTP $code)."
  echo "!! Pick a VPS region where Bybit works (e.g. EU) before continuing."
  exit 1
fi

echo "== 1) Packages =="
apt-get update -qq && apt-get install -y -qq python3-venv python3-pip git curl

echo "== 2) App user + code =="
id -u trader &>/dev/null || useradd -r -m -s /usr/sbin/nologin trader
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull
else
  git clone "$REPO_URL" "$APP_DIR"
fi

echo "== 3) Python env =="
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/server/requirements.txt"

echo "== 4) .env =="
if [ ! -f "$APP_DIR/server/.env" ]; then
  cp "$APP_DIR/server/.env.example" "$APP_DIR/server/.env"
  chmod 600 "$APP_DIR/server/.env"
  echo ">> EDIT $APP_DIR/server/.env now (API keys, panel password, telegram)."
fi
chown -R trader:trader "$APP_DIR"

echo "== 5) systemd service =="
cp "$APP_DIR/deploy/tradingbot.service" /etc/systemd/system/tradingbot.service
systemctl daemon-reload
systemctl enable tradingbot

echo "== 6) Tailscale (panel visible only to your devices) =="
if ! command -v tailscale &>/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
echo ">> Run: tailscale up   (then open the printed login link once)"

echo "== 7) Firewall: block the panel from the public internet =="
if command -v ufw &>/dev/null; then
  ufw allow ssh >/dev/null
  ufw allow in on tailscale0 to any port 8080 proto tcp >/dev/null
  ufw deny 8080/tcp >/dev/null
  ufw --force enable >/dev/null
  echo "ufw: 8080 allowed on tailscale0 only"
fi

echo
echo "DONE. Next steps:"
echo "  1. nano $APP_DIR/server/.env     (fill in keys + password)"
echo "  2. tailscale up                  (link this VPS to your Tailscale)"
echo "  3. systemctl start tradingbot"
echo "  4. Panel: http://<tailscale-ip>:8080 from your phone/PC on Tailscale"
