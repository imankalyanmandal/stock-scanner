# Swing-setup stock scanner (NSE)

Scans **Nifty 50 + Nifty Next 50 + Nifty Midcap 150** (~250 stocks) every day at **3:00 AM IST**
and writes a ranked report of stocks that passed the gates.

## Quick start

```bash
pip install -r requirements.txt
python run_scan.py --limit 20 --no-news   # quick smoke test on 20 stocks
python run_scan.py                        # full scan now
python scheduler.py                       # keep running; scans at 3:00 AM IST
python scheduler.py --run-now             # scan once immediately, then keep the schedule
python -m pytest -q                       # offline tests on synthetic data
```

Reports land in `reports/scan_<session-date>.{html,csv,json}`. Logs go to `logs/scanner.log`.

### Scheduling options

**Docker (recommended for a server):**
```bash
docker build -t stock-scanner .
docker run -d --name scanner --restart unless-stopped \
  -v $PWD/reports:/app/reports -v $PWD/logs:/app/logs -v $PWD/data:/app/data stock-scanner
```

**Plain cron instead of `scheduler.py`:**
```
CRON_TZ=Asia/Kolkata
0 3 * * 2-6  cd /path/to/stock-scanner && /usr/bin/python3 run_scan.py >> logs/cron.log 2>&1
```

The default runs **Tue–Sat**, so each run scans the previous Mon–Fri session. Set
`schedule.day_of_week: "*"` in `config.yaml` to run literally every day. The Sunday and
Monday runs would just re-scan Friday's session.

## Telegram notifications

After each scan you get a summary message (regime, picks, entry/stop/target, news and event
flags) plus the full HTML report as an attachment. If a scan fails, you get an alert with a
link to the logs.

**One-time setup:**
1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. Copy the
   **bot token** it gives you (looks like `123456789:AAH...`).
2. Open a chat with your new bot and send it any message (e.g. `hi`). Bots can't message you
   until you've messaged them first.
3. Get your **chat id**: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and find
   `"chat":{"id":123456789,...}`. That number is your chat id.
4. Add both as GitHub secrets: repo → **Settings → Secrets and variables → Actions → New
   repository secret**:
   - `TELEGRAM_BOT_TOKEN` = the token
   - `TELEGRAM_CHAT_ID` = the chat id

For local runs, export the same two variables in your shell instead. If they're missing,
the scan still runs and just skips the messages. Use `--no-notify` to skip them on purpose.
Message settings are under `notify.telegram` in `config.yaml`.

## Pipeline

| Step | What happens | Where |
|---|---|---|
| 0. Market gate | Nifty 50 / Nifty 500 vs 20 & 50 DMA, % of universe above 20 DMA, per-sector breadth → `strong` / `neutral` / `weak`. In a weak market, the min score rises (or the run is skipped) and only breakout or pullback setups qualify. | `market.py` |
| 1. Universe | Constituent CSVs from NSE, cached in `data/` with fallback to the cache | `universe.py` |
| 2. Hygiene gates | Price ≥ ₹50, 20-day **median** turnover ≥ ₹10 Cr, avg volume ≥ 1 lakh | `filters.py` |
| 3. Core rule | Last 2 sessions green (`green_mode`: `prev_close` / `candle` / `both`). Prices are split/bonus/dividend adjusted. | `filters.py` |
| 4. Setup tag | **breakout** (close above prior 20-bar high) / **pullback** (touched a rising 20 DMA, closed above it) / none | `filters.py` |
| 5. Quality | Volume ratio, close location in day's range, delivery %, higher highs/lows; flags for gap-up with weak close, abnormal gap, no-range day (possible circuit) | `filters.py` |
| 6. Not chasing | Rejects: 2-day gain > 8%, RSI > 75, more than 2 ATR above 20 DMA. RSI 50–70 is scored. High ATR% is flagged. | `filters.py` |
| 7. Trend & RS | Close vs 20/50 DMA; 5- and 20-day return vs Nifty **and** vs sector peers; bonus if RS improving | `filters.py` |
| 8. Risk/reward | Entry = close. Stop = under the breakout level, or under the 5-bar low (≥1 ATR away). Target = nearest significant swing high, or 4 ATR at fresh highs. Rejects if R:R < 2 or stop > 3 ATR. | `filters.py` |
| 9. Events | Earnings / ex-dividend within 5 days → `event_soon` flag | `enrich.py` |
| 10. News | Google News headlines (last 3 days), freshness, source, links | `enrich.py` |

Survivors are sorted by setup type (breakout, then pullback, then none), then by score
(0–100, weights in `config.yaml`). Steps 9–10 only run on survivors, to keep network calls low.

## Tuning

Every threshold is in `config.yaml`, and the scheduler re-reads it on each run. The HTML report
has a **"Why stocks were rejected"** section. Check it after the first few real runs. If
`reward_risk_below_min` or `stretched_from_20dma` is rejecting nearly everything, loosen
that rule before touching the others.

## Not covered yet

- **ASM/GSM lists, F&O ban list, circuit-band history.** NSE serves these behind anti-bot
  protection, so they need a broker API or a scraper with session handling. A no-range day is
  flagged as `possible_circuit` meanwhile.
- **Bid-ask spread / impact cost.** Not in EOD data; needs a broker API.
- **Sector indices.** Sector trend and breadth are computed from peers in the same NSE
  industry within the universe, not from official sector index prices.
- **News sentiment and materiality.** `enrich.classify_news()` is a stub returning `None`.
  Plug in your LLM chain there: positive/negative/neutral, official filing vs media report,
  and whether the news explains the move. Keep it score-only.
- **Regulatory / litigation / fundraising disclosures.** These need NSE corporate
  announcements, which sit behind the same anti-bot protection as above.

## Data sources

- Prices: Yahoo Finance via `yfinance` (`.NS` tickers, adjusted). It's free and unofficial, so
  occasional gaps happen and missing symbols are logged. For production, swap `data.py` to
  Kite/Upstox.
- Delivery %: NSE full bhavcopy for the session date. If unavailable, the delivery score is set
  to neutral and a warning is logged.

Screening output only, not investment advice. Entry, stop and target are mechanical estimates.
