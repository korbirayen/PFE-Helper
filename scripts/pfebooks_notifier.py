"""Pfebooks catalogue notifier.

Scrapes https://pfebooks.com/catalogue/ (and paginated pages),
extracts PFE Book entries with their publish date, filters to the
last N days, and sends notifications to Telegram. It keeps a small
state file under data/ so it only announces new books and sends a
"Nothing new" message when there are no new entries.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from .utils import DATA_DIR, load_env

USER_AGENT = "PFE-AggregatorBot/1.0 (+https://github.com/korbirayen/PFE-Helper)"
BASE_CATALOGUE_URL = "https://pfebooks.com/catalogue/"
STATE_FILE = DATA_DIR / "pfebooks_state.json"


@dataclass
class BookEntry:
    title: str
    url: str
    published_at: Optional[dt.datetime]


def _fetch(url: str, timeout: int = 15) -> Optional[requests.Response]:
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code >= 400:
            logging.warning("HTTP %s for %s", resp.status_code, url)
            return None
        return resp
    except Exception as exc:  # pragma: no cover - network dependent
        logging.warning("Error fetching %s: %s", url, exc)
        return None


def parse_book_page(url: str) -> Optional[dt.datetime]:
    """Try to parse publish date from a pfebooks book page.

    We try several strategies:
    - <meta property="article:published_time" ...>
    - <time datetime="..."> or <time>text</time>
    - JSON-LD with datePublished.
    Returns None if no date could be parsed.
    """

    resp = _fetch(url)
    if not resp:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")

    # Strategy 1: meta property
    meta = soup.find("meta", attrs={"property": "article:published_time"})
    if meta and meta.get("content"):
        try:
            return dt.datetime.fromisoformat(meta["content"].replace("Z", "+00:00"))
        except Exception:
            pass

    # Strategy 2: <time datetime="...">
    t = soup.find("time", attrs={"datetime": True})
    if t is not None:
        try:
            return dt.datetime.fromisoformat(t["datetime"].replace("Z", "+00:00"))
        except Exception:
            pass

    # Strategy 3: <time>text</time>
    t = soup.find("time")
    if t and t.text.strip():
        txt = t.text.strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return dt.datetime.strptime(txt, fmt)
            except Exception:
                continue

    # Strategy 4: JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except Exception:
            continue
        if isinstance(data, dict) and "datePublished" in data:
            try:
                return dt.datetime.fromisoformat(str(data["datePublished"]).replace("Z", "+00:00"))
            except Exception:
                continue

    return None


def scrape_catalogue(max_pages: int = 10) -> List[BookEntry]:
    """Scrape the catalogue index pages and return book entries.

    Pagination is done via /catalogue/page/2/, /page/3/, ... until
    no book links are found or max_pages is reached.
    """

    entries: List[BookEntry] = []
    page = 1
    while page <= max_pages:
        url = BASE_CATALOGUE_URL if page == 1 else f"{BASE_CATALOGUE_URL}page/{page}/"
        logging.info("Scraping catalogue page %s: %s", page, url)
        resp = _fetch(url)
        if not resp:
            break
        soup = BeautifulSoup(resp.text, "html.parser")

        # Heuristic: links under main content that look like catalogue items
        page_entries = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/catalogue/202" not in href:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            # Avoid duplicates within page
            if any(e.url == href for e in entries) or any(e.url == href for e in page_entries):
                continue
            page_entries.append(BookEntry(title=title, url=href, published_at=None))

        if not page_entries:
            break

        # Enrich with publish dates
        for e in page_entries:
            e.published_at = parse_book_page(e.url)
            entries.append(e)

        page += 1

    return entries


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"seen_urls": [], "last_run": None}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_urls": [], "last_run": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = dt.datetime.utcnow().isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def post_telegram_message(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logging.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; cannot post to Telegram.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code >= 400:
            logging.warning("Telegram API error %s: %s", resp.status_code, resp.text)
            return False
        return True
    except Exception as exc:  # pragma: no cover
        logging.warning("Error posting to Telegram: %s", exc)
        return False


def notify(window_days: int = 5) -> None:
    # Work with naive UTC datetimes for comparisons
    now = dt.datetime.utcnow()
    since = now - dt.timedelta(days=window_days)

    state = load_state()
    seen_urls = set(state.get("seen_urls", []))

    entries = scrape_catalogue()
    logging.info("Scraped %d catalogue entries", len(entries))

    # Filter by publish date window
    fresh: List[BookEntry] = []
    for e in entries:
        if e.published_at is None:
            continue
        pub = e.published_at
        # Normalize to naive UTC
        if pub.tzinfo is not None:
            try:
                pub = pub.astimezone(dt.timezone.utc).replace(tzinfo=None)
            except Exception:
                pub = pub.replace(tzinfo=None)
        if pub < since:
            continue
        e.published_at = pub
        fresh.append(e)

    # New entries = within window and not seen before
    new_entries = [e for e in fresh if e.url not in seen_urls]

    if not new_entries:
        logging.info("No new PFE Books found; sending 'Nothing new'.")
        post_telegram_message("PFEBooks catalogue: Nothing new in the last 5 days.")
        save_state({"seen_urls": list(seen_urls)})
        return

    for e in new_entries:
        published_str = e.published_at.strftime("%Y-%m-%d") if e.published_at else "unknown date"
        msg = (
            f"New PFE Book: {e.title}\n"
            f"Date: {published_str}\n"
            f"Link: {e.url}"
        )
        post_telegram_message(msg)
        seen_urls.add(e.url)

    save_state({"seen_urls": list(seen_urls)})


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Notify about new PFEBooks catalogue entries.")
    p.add_argument("--window-days", type=int, default=5, help="Number of days back to consider a book as new.")
    p.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return p


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")


def main(argv: Optional[list[str]] = None) -> int:
    load_env()
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    setup_logging(args.debug)

    notify(window_days=args.window_days)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
