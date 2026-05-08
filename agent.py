#!/usr/bin/env python3
"""EU project/topic watcher.

Starts from the EU Funding & Tenders topic-announcements page and follows
relevant portal-internal links to discover new announcement/topic pages.
Matches page text against keywords and sends alerts when new matches appear.

Default channel: Telegram (optional).
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # pragma: no cover
    sync_playwright = None

SEED_URL = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/how-to-participate/topic-announcements/50133351"
DEFAULT_URLS = [SEED_URL]
DEFAULT_KEYWORDS = [
    "governance",
    "public policy and administration",
    "science, technology and innovation policy",
    "digital sovereignty",
    "open strategic autonomy",
    "competitiveness",
    "sustainability",
    "ethics of care",
    "just and resilient cities",
    "architectural and urban planning education",
    "design competencies",
    "participatory design",
    "living labs",
    "spatial inequalities",
    "dual-use energy systems",
    "energy resilience",
    "crisis-response energy systems",
    "modular energy systems",
    "capacity building",
    "nd-fe-b magnets",
    "rare-earth recycling",
    "circular manufacturing",
    "magnet processing",
    "hddr recycling",
    "additive manufacturing",
    "critical raw materials",
    "electrical machines",
    "materials circularity",
    "in-space servicing",
    "satellite re-use",
    "system-level architecture",
    "space system resilience",
    "sustainable space systems",
    "ai soft skills",
    "ai literacy",
    "advanced research method capacity",
    "ai-empowered research management",
    "knowledge valorisation",
    "responsible ai",
    "lignin and cellulose valorisation",
    "bio-based polymers",
    "green chemistry",
    "sustainable materials processing",
    "mechanochemistry",
    "reactive extrusion",
    "predictive design",
    "direct current microgrids",
    "sustainable electrification",
    "energy efficiency",
]

USER_AGENT = "Mozilla/5.0 (compatible; EUProjectWatcher/1.0)"
MAX_CRAWL_PAGES = int(os.getenv("MAX_CRAWL_PAGES", "20"))
MAX_CRAWL_DEPTH = int(os.getenv("MAX_CRAWL_DEPTH", "2"))
ALLOWED_NETLOCS = {"ec.europa.eu", "webgate.ec.europa.eu"}


@dataclass(frozen=True)
class Hit:
    url: str
    title: str
    snippet: str
    matched_keywords: tuple[str, ...]

    def key(self) -> str:
        return f"{self.url}::{self.title}"


def env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return [part.strip() for part in raw.split(",") if part.strip()]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    low = normalize(text)
    hits: list[str] = []
    for kw in keywords:
        if normalize(kw) in low:
            hits.append(kw)
    return hits


def fetch_html_requests(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=40)
    r.raise_for_status()
    return r.text


def fetch_html_playwright(url: str) -> str:
    if sync_playwright is None:
        raise RuntimeError("playwright is not installed")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(url, wait_until="networkidle", timeout=60000)
        html = page.content()
        browser.close()
        return html


def fetch_html(url: str) -> str:
    html = fetch_html_requests(url)
    # If the page looks too empty, try Playwright as a fallback.
    if len(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)) < 500:
        try:
            return fetch_html_playwright(url)
        except Exception:
            pass
    return html


def extract_links(base_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue
        absolute = urldefrag(urljoin(base_url, href))[0]
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc not in ALLOWED_NETLOCS:
            continue
        if "/portal/screen/" not in parsed.path:
            continue
        links.append(absolute)
    return list(dict.fromkeys(links))


def extract_candidates(url: str, html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url

    candidates: list[tuple[str, str]] = []
    page_text = soup.get_text(" ", strip=True)
    candidates.append((title, page_text[:8000]))

    selectors = ["a", "h1", "h2", "h3", "h4", "li", "article", "section", "div"]
    seen: set[str] = set()
    for sel in selectors:
        for el in soup.select(sel):
            txt = el.get_text(" ", strip=True)
            if not txt or len(txt) < 20:
                continue
            txt_norm = normalize(txt)
            if txt_norm in seen:
                continue
            seen.add(txt_norm)
            link = el.find("a", href=True)
            item_url = urljoin(url, link["href"]) if link else url
            candidates.append((item_url, txt_norm))

    return candidates


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_state(path: Path, seen: set[str]) -> None:
    path.write_text(json.dumps(sorted(seen), indent=2, ensure_ascii=False), encoding="utf-8")


def send_telegram(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print(message)
        return
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(api, json={"chat_id": chat_id, "text": message}, timeout=30)
    resp.raise_for_status()


def crawl(start_urls: Iterable[str], max_pages: int = MAX_CRAWL_PAGES, max_depth: int = MAX_CRAWL_DEPTH) -> list[tuple[str, str]]:
    """Breadth-first crawl within the EU portal, starting from the announcement page."""
    queue = deque((url, 0) for url in start_urls)
    visited: set[str] = set()
    pages: list[tuple[str, str]] = []

    while queue and len(visited) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        try:
            html = fetch_html(url)
        except Exception as exc:
            print(f"[warn] failed on {url}: {exc}", file=sys.stderr)
            continue

        pages.append((url, html))

        if depth >= max_depth:
            continue

        for link in extract_links(url, html):
            if link not in visited:
                queue.append((link, depth + 1))

    return pages


def scan(urls: Iterable[str], keywords: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    for url, html in crawl(urls):
        for item_url, text in extract_candidates(url, html):
            matched = keyword_hits(text, keywords)
            if matched:
                title = text[:180]
                snippet = text[:350]
                hits.append(Hit(item_url, title, snippet, tuple(matched)))
    return hits


def format_message(new_hits: list[Hit]) -> str:
    lines = [
        f"EU watcher: {len(new_hits)} new match(es) at {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for hit in new_hits[:10]:
        lines.append(f"- {hit.title}")
        lines.append(f"  URL: {hit.url}")
        lines.append(f"  Keywords: {', '.join(hit.matched_keywords)}")
        if hit.snippet:
            lines.append(f"  Snippet: {hit.snippet[:220]}")
        lines.append("")
    return "\n".join(lines).strip()


def main() -> int:
    urls = env_list("WATCH_URLS", DEFAULT_URLS)
    keywords = env_list("KEYWORDS", DEFAULT_KEYWORDS)
    state_file = Path(os.getenv("STATE_FILE", "seen_state.json"))

    seen = load_state(state_file)
    hits = scan(urls, keywords)

    new_hits = [hit for hit in hits if hit.key() not in seen]
    for hit in new_hits:
        seen.add(hit.key())

    save_state(state_file, seen)

    if new_hits:
        send_telegram(format_message(new_hits))
        return 0

    print("No new matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
