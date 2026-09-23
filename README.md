# JobBot

A small, self-hosted job-hunting bot for the Kenyan market. Once a day it
scrapes analyst/engineer roles from local job boards, keeps only new postings
within a configurable age window, and emails you a formatted digest. An optional
auto-apply step can draft and send tailored cover emails with Claude.

> Personal side project. Scraping third-party sites can break when their markup
> changes and may be against their Terms of Service — use responsibly and at
> your own risk.

## Pipeline

`scrape → filter → notify → (optional) apply`

1. **scrape** — fetch raw jobs from the enabled sources
2. **filter** — deduplicate (SQLite), drop postings older than `MAX_AGE_DAYS`, and keep only those matching your profile keywords
3. **notify** — send an HTML + plain-text digest over Gmail SMTP
4. **apply** — *(off by default)* generate tailored cover emails with Claude and send them via the Gmail API

## Sources & limitations

Scraping happens over plain HTTP by default; set `USE_SELENIUM=true` to render
JavaScript pages with headless Chrome.

| Source          | How it's read                | Notes |
| --------------- | ---------------------------- | ----- |
| **MyJobMag**    | plain HTTP, server-rendered  | Best coverage; its search genuinely filters by keyword. |
| **Fuzu**        | headless Chrome (`USE_SELENIUM=true`) | Keyword search only works in a browser. Fuzu rate-limits automated access, so results vary run to run. |
| **BrighterMonday** | plain HTTP                | Behind Cloudflare — only a couple of featured cards are reachable; the full list needs anti-bot evasion (out of scope). |

Relevance is always enforced locally by your `profile.json` keywords, so
imprecise sources are filtered down regardless.

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate        # Windows Git Bash; use .venv/bin/activate on Linux/macOS
pip install -r requirements.txt

cp .env.template .env                 # then fill in your values
cp profile.example.json profile.json  # then fill in your details
```

- **`.env`** — email/SMTP, optional Anthropic key, scraper settings, schedule.
  Use a Gmail **App Password** (not your account password); requires 2FA.
- **`profile.json`** — your target roles, keywords, and CV. Both files are
  gitignored and stay local.

## Running

```bash
python run.py --now      # run the pipeline immediately
python run.py            # run now, then daily at RUN_TIME (in-process scheduler)
```

For deployment, prefer running `run.py --now` on an external scheduler — see
**Docker** below and `DEPLOY.md`.

## Docker (recommended for deployment)

Runs the pipeline daily via an in-container cron (`supercronic`), with Chrome
preinstalled for Selenium, state on a volume, and secrets injected from `.env`.

```bash
docker compose build
docker compose up -d           # daily run at RUN_TIME (TZ set in docker-compose.yml)
docker compose logs -f
docker compose run --rm jobbot python run.py --now   # test a real run
```

Full details, including how state persists and how to enable auto-apply, are in
[`DEPLOY.md`](./DEPLOY.md).

## Configuration reference (`.env`)

| Variable | Purpose |
| --- | --- |
| `NOTIFY_EMAIL` | Where the digest is sent |
| `GMAIL_SENDER` / `GMAIL_APP_PASSWORD` | Gmail SMTP sender + app password |
| `ANTHROPIC_API_KEY` | Only for auto-apply cover-email generation |
| `SCRAPE_SOURCES` | Comma-separated: `fuzu,myjobmag,brightermonday` |
| `ROLE_KEYWORDS` | Comma-separated keywords to match |
| `LOCATION` | City filter |
| `MAX_AGE_DAYS` | Drop postings older than this many days |
| `RUN_TIME` | Daily run time, `HH:MM` |
| `USE_SELENIUM` / `SELENIUM_HEADLESS` | Enable headless-Chrome rendering (Fuzu) |


