# Crypto RSI + Trend Alert Bot (paper / alert-only)

A bot that watches several coins and sends you **Telegram alerts** telling you
when it's a good moment to **buy the dip** or **sell the top** — plus an hourly
market pulse and a twice-daily political/macro news read. It **never places real
trades**. You execute manually; the bot only watches and pings you.

Built for paper testing: every hourly check is logged to a CSV so you can
review whether the strategy actually works before risking real money.

---

## What it does

**Every hour** (`bot.py run`) it sends **one message — one sentence per coin**
with its trend, RSI, and a plain verdict. For each coin it pulls 1-hour + daily
candles from Bybit (no key needed) and computes (via the `ta` library — nothing
hand-coded) **RSI(14)** on 1h and the **trend** on the daily timeframe
(`MA50`/`MA200` for direction, `ADX(14)` for strength). It also logs one row per
coin to `ledger.csv` and reads any `/buy` `/sell` commands you sent.

**Paper auto-trader (simulated money — never touches an exchange):** each hour,
after the signals, it opens a simulated LONG on 🟢 BUY / SHORT on 🔴 SELL (20% of
paper equity), protects it with a 2% stop-loss, then rides winners with a
**trailing stop** — once a trade is +5% it trails 4% below the best price, so it
keeps running while the trend holds and exits on a reversal (locking in ~1%
once armed). It books P&L into a virtual account, logs closed trades to
`trades.csv`, and Telegrams each open/close. All levels are configurable
(`STOP_PCT`, `TRAIL_ACTIVATE_PCT`, `TRAIL_DISTANCE_PCT`; set `TRAIL_ON = False`
for a fixed `TARGET_PCT` take-profit instead). Toggle the whole thing with
`PAPER_TRADING`. **No exchange connection — cannot touch real money.**

**Web dashboard:** a free GitHub Pages site
(`https://alxander0022-commits.github.io/crypto-rsi-bot/`) shows live signals,
Fear & Greed, open positions with P&L, trade history, equity and win rate. The
bot writes `docs/data.json` each run; enable Pages with source **main / `/docs`**.

**Every 12 hours** (`bot.py news`) — a market-mood report:
- The **crypto Fear & Greed Index** (free, no key) as a text gauge **plus the
  official chart image**, so you can see at a glance if people are fearful or greedy.
- A read on **market sentiment** (buying / selling / panicking), the political /
  economic direction, and a **BULLISH / BEARISH / NEUTRAL** call per coin —
  researched live via the **Claude API with web search** (needs an API key).

### The verdict per coin (trend-aware mean reversion)

| Trend (daily) | RSI(1h) | Verdict |
|---|---|---|
| **UPTREND** | RSI crosses back **up** through `RSI_BUY` (30) | 🟢 **time to BUY** |
| **DOWNTREND** | RSI crosses back **down** through `RSI_SELL` (70) | 🔴 **time to SELL** |
| anything else | — | ⛔ **stay out** |

**Confirmation (`CONFIRM_REVERSAL = True`):** the signal fires only when RSI has
gone past the level and then **crossed back through it** — i.e. the extreme is
rolling over — so you don't act while a spike is still running. It's a discrete
crossover event: it shows on the hour the cross happens, then reverts to "stay
out". Set `CONFIRM_REVERSAL = False` for the simpler "RSI is beyond the level"
trigger.

The idea: in an uptrend, oversold dips that start bouncing are buying chances; in
a downtrend, overbought bounces that start fading are selling chances; otherwise
stay out. Trend direction:
- Price **above** MA200 **and** MA50 **above** MA200 → **UPTREND**
- Price **below** MA200 **and** MA50 **below** MA200 → **DOWNTREND**
- anything else → **SIDEWAYS**
- **ADX below 20** overrides to **SIDEWAYS** (no real trend).

> **Note on RSI 80:** overbought RSI 80 is a high bar — many downtrend bounces
> top out at 60–70 without ever hitting 80, so 🔴 SELL will be rarer than 🟢 BUY.
> Lower `RSI_SELL` (e.g. 70) if you want more.

### Example messages
A coin close to (but not yet at) a trigger shows a `⚠️ setup building` heads-up
(within `SETUP_WARN` points of the level, in the right trend).

```
BTC — trend DOWN, RSI 68 → ⛔ stay out ⚠️ SELL setup building
ETH — trend DOWN, RSI 34 → ⛔ stay out
SOL — trend UP, RSI 27 → 🟢 time to BUY
Fear & Greed: 20/100 — Extreme Fear 😱

🗞️ 12h market check
Fear & Greed: 20/100 — Extreme Fear 😱  (yesterday 27 ▼)
[████░░░░░░░░░░░░░░░░]

Mood: traders de-risking, some panic selling on rate fears
BTC: BEARISH — Fed hawkish, ETF outflows
ETH: NEUTRAL — awaiting upgrade news
SOL: BEARISH — risk-off, broad selloff
• ... headline (source) ...
Overall: risk-off
```
(plus the official Fear & Greed chart sent as an image)

---

## Setup

### 0. Install Python (one-time)

Python **3.12 is already installed** on this machine (`D:\trading` was set up
with it). Confirm in a fresh PowerShell:
```powershell
python --version
```
On a new machine, install Python **3.11+** from
<https://www.python.org/downloads/> (tick **"Add python.exe to PATH"**) or
`winget install Python.Python.3.12`, then reopen PowerShell.

### 1. Install dependencies
```powershell
cd D:\trading
python -m pip install -r requirements.txt
```

### 2. Create your Telegram bot & get your chat ID

1. In Telegram, message **@BotFather** → `/newbot` → follow prompts.
   It gives you a **bot token** like `123456789:AAE...`.
2. Send your new bot any message (e.g. "hi") so it has a chat to reply to.
3. Get your **chat ID**: open in a browser (paste your token):
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   Look for `"chat":{"id":123456789,...}` — that number is your chat ID.

### 3. Add your credentials

**Option A — environment variables (recommended, keeps secrets out of files):**
```powershell
$env:TELEGRAM_TOKEN   = "123456789:AAE..."
$env:TELEGRAM_CHAT_ID = "123456789"
```
(These last only for the current PowerShell window. For a permanent local
setup use the scheduled `.bat` approach below, or set them via
*System Properties → Environment Variables*.)

**Option B — paste into `config.py`:** replace `PASTE_YOUR_BOT_TOKEN_HERE`
and `PASTE_YOUR_CHAT_ID_HERE`.

### 4. First milestone — send a test message ✅
```powershell
python bot.py test
```
You should get a Telegram ping within a second or two. **Do this before
anything else** — confirm alerts reach your phone before trusting the market
logic.

### 5. Run one real check manually
```powershell
python bot.py run
```
It prints a summary line per coin, appends rows to `ledger.csv`, sends the
hourly pulse, and pings you if a BUY/SELL signal fires.

### 6. Add an Anthropic API key (for the 12-hour report's commentary)

`python bot.py news` sends the **Fear & Greed gauge + chart with no key**. To
add the live market-mood commentary (buying/selling/panicking + per-coin
BULLISH/BEARISH + headlines), it uses the **Claude API with web search**. Create
a key at <https://console.anthropic.com> → **API Keys**, then either:
- set it for the window: `$env:ANTHROPIC_API_KEY = "sk-ant-..."`, or
- paste it into `config.py` (replace `PASTE_YOUR_ANTHROPIC_KEY_HERE`).

Test it: `python bot.py news`. Rough cost ≈ **$0.10–0.30 per run** (~2 runs/day).
The hourly BUY/SELL message needs no key at all.

---

## Recording your manual trades

The bot fires directional BUY/SELL signals; you decide and trade. If you want
to keep a paper record of a position (P&L in `/status`), tell the bot:

**From your phone (Telegram):** reply to the bot with:
- `/buy BTCUSDT` — mark bought at the current price (`/buy SOL 77` for a price)
- `/sell SOLUSDT` — mark that you exited that coin
- `/status` — show your recorded positions and P&L
- `/help` — list commands

(Commands are read at the top of each hourly run, so they take effect on the
next check — usually within the hour.)

**From the command line:**
```powershell
python bot.py buy BTCUSDT 61200
python bot.py sell SOLUSDT
python bot.py status
python bot.py snapshot   # ping current reading for all coins now
python bot.py news       # send the 12h macro report now
```

Positions are stored in `state.json`. They're for your record only — the
directional signals fire independently of whether you're "holding".

---

## Scheduling it to run hourly (this is essential)

The program does **not** loop on its own — a scheduler must fire it every hour.
Pick one:

### A) Windows Task Scheduler (simplest here — state stays on your disk)

`run_bot.bat` is included; it cd's into the project and logs to `bot.log`.
Register it to run hourly (run PowerShell **as Administrator**):

```powershell
schtasks /Create /TN "RSIBot" /TR "D:\trading\run_bot.bat" /SC HOURLY /RL LIMITED /F
```

There is also a **12-hour news task** (`run_news.bat` → `bot.py news`),
registered with:
```powershell
schtasks /Create /TN "RSIBot-News" /TR "D:\trading\run_news.bat" /SC HOURLY /MO 12 /RL LIMITED /F
```

To make sure they run 24/7 even after reboots, set your PC to not sleep, or add
`/RU <yourWindowsUser>` and a stored password. Check them:
```powershell
schtasks /Run /TN "RSIBot"        # run the hourly check once now
schtasks /Run /TN "RSIBot-News"   # run the 12h news report once now
Get-Content D:\trading\bot.log -Tail 20
```
> Because the scheduled tasks run in their own context, put your credentials in
> `config.py` (`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `ANTHROPIC_API_KEY`) — or
> set them as **System** environment variables so the tasks can see them.
> Window-only `$env:` vars won't reach a scheduled task.

### B) GitHub Actions (runs in the cloud, PC can be off)

Ready workflows: `.github/workflows/hourly.yml` (the hourly check) and
`.github/workflows/news.yml` (the 12-hour report).
1. Push this folder to a GitHub repo.
2. Repo → **Settings → Secrets and variables → Actions** → add
   `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, and `ANTHROPIC_API_KEY`.
3. The hourly workflow **commits `state.json` + `ledger.csv` back to the repo**
   so your positions and paper record persist between runs (cloud runners are
   wiped each time).

> GitHub's scheduled runs can be delayed a few minutes and are best-effort —
> fine for an hourly strategy, but don't expect second-level precision.

### C) Linux/Mac cron
```
5 * * * * cd /path/to/trading && /usr/bin/python3 bot.py run >> bot.log 2>&1
```

---

## Config (all at the top of `config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `SYMBOLS` | `[BTCUSDT, ETHUSDT, SOLUSDT]` | Coins to watch — each alerts independently. |
| `RSI_BUY` | `30` | Oversold level for the 🟢 BUY crossover (uptrend). |
| `RSI_SELL` | `70` | Overbought level for the 🔴 SELL crossover (downtrend). |
| `CONFIRM_REVERSAL` | `True` | Require RSI to cross back through the level (rollover) before signalling. |
| `TREND_TIMEFRAME` | `D` | Trend timeframe. `D` = daily, `240` = 4H. |
| `HOURLY_REPORT` | `True` | Send the trend+RSI pulse for all coins every hour. |
| `REPEAT_ALERTS` | `False` | Re-ping every hour vs only on change. |
| `NEWS_REPORT_ON` | `True` | Enable the 12h macro report (needs an API key). |
| `NEWS_MODEL` | `claude-opus-4-8` | Model for the news research. |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | env / paste | Your Telegram creds. |
| `ANTHROPIC_API_KEY` | env / paste | For the 12h news report. |

`TARGET_PCT` / `DISASTER_STOP_*` remain in `config.py` but no longer drive
alerts — they only affect the P&L shown in `/status`.

**Testing different settings:** to compare `RSI_SELL` 80 vs 70, or BTC vs ETH vs
SOL, just change the value and let it collect a few weeks of `ledger.csv` rows.
Rename `ledger.csv` between experiments so you can compare cleanly.

---

## The paper-trading ledger

`ledger.csv` gets one row per hourly check:

```
timestamp, symbol, price, rsi, trend, adx, signal, action
```

`signal` is `BUY/SELL/NONE` and `action` is the plain verdict
(`buy` / `sell` / `stay_out`). This is your record for judging whether the
strategy would have made money. The 12h news reports are appended to
`news_log.txt`.

---

## Files

| File | Purpose |
|---|---|
| `config.py` | All settings. |
| `bot.py` | Main entry / hourly check / CLI commands. |
| `data.py` | Bybit candle fetching. |
| `indicators.py` | RSI / MA / ADX via the `ta` library. |
| `notify.py` | Telegram send + command polling. |
| `news.py` | 12h macro/political report (Claude API + web search). |
| `state.py` | Position store (`state.json`). |
| `ledger.py` | CSV ledger writer. |
| `run_bot.bat` / `run_news.bat` | Windows Task Scheduler wrappers. |
| `.github/workflows/hourly.yml` / `news.yml` | Cloud schedulers. |

---

## ⚠️ Disclaimer

This is an educational paper-testing / alerting tool, **not financial advice**
and **not an automated trader**. It never touches your exchange account or
funds. RSI + trend signals are frequently wrong; a "🟢 BUY the dip" is a prompt
to do your own analysis, not a guarantee. Test on paper first.
