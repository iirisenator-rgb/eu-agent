# EU Project Watcher

A small agent that starts from the EU Funding & Tenders topic-announcements page and follows portal-internal links to find new announcement/topic pages.
It matches page text against your keyword list and sends a Telegram alert when it finds a new match.

## Target page

The watcher starts from:

`https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/how-to-participate/topic-announcements/50133351`

## What it does
- fetches the starting page with `requests`
- falls back to Playwright when the page looks JS-heavy
- crawls a small number of same-portal pages linked from the starting page
- extracts visible text and likely items
- matches against your keyword list
- remembers previously seen matches in a local JSON file
- sends alerts to Telegram, or prints to stdout if Telegram is not configured

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Run once

```bash
export WATCH_URLS="https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/how-to-participate/topic-announcements/50133351"
export KEYWORDS="governance,public policy and administration,digital sovereignty,energy resilience,ai literacy,green chemistry"
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python agent.py
```

## Crawl tuning

You can adjust how far the agent follows internal links:

```bash
export MAX_CRAWL_PAGES=20
export MAX_CRAWL_DEPTH=2
```

## Schedule with cron

Run every day at 07:10:

```cron
10 7 * * * /path/to/.venv/bin/python /path/to/eu_project_watcher/agent.py
```

## Notes
- For very dynamic portal pages, Playwright is usually more reliable than plain HTML parsing.
- The script stores state in `seen_state.json` by default.
- If the portal changes its structure, start by increasing `MAX_CRAWL_DEPTH` a little.
