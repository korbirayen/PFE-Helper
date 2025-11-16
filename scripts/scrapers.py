"""Web scrapers for various PFE sources.

Each scraper returns a list of project dicts with keys:
    title, company, link, description, contact_email, source_url, date_scraped
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from .utils import DATA_DIR


USER_AGENT = "PFE-AggregatorBot/1.0 (+https://github.com/yourusername/pfe-helper)"


@dataclass
class ScrapeResult:
    title: str
    company: str
    link: str
    description: str
    contact_email: str | None
    source_url: str
    date_scraped: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "company": self.company,
            "link": self.link,
            "description": self.description,
            "contact_email": self.contact_email or "",
            "source_url": self.source_url,
            "date_scraped": self.date_scraped,
        }


def _log_link_status(url: str, status: str, message: str = "") -> None:
    path = DATA_DIR / "link_statuses.csv"
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        if new:
            f.write("timestamp,url,status,message\n")
        ts = dt.datetime.utcnow().isoformat()
        safe_msg = message.replace("\n", " ").replace("\r", " ")
        f.write(f"{ts},{url},{status},{safe_msg}\n")


def _fetch(url: str, timeout: int = 15) -> Optional[requests.Response]:
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(2):  # retry once
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code >= 400:
                _log_link_status(url, f"HTTP_{resp.status_code}")
                return None
            return resp
        except Exception as exc:  # pragma: no cover - depends on network
            logging.warning("Error fetching %s on attempt %s: %s", url, attempt + 1, exc)
            if attempt == 0:
                time.sleep(2)
                continue
            _log_link_status(url, "ERROR", str(exc))
            return None
    return None


def _scrape_pfebook(url: str) -> List[ScrapeResult]:
    resp = _fetch(url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    items: List[ScrapeResult] = []
    date = dt.datetime.utcnow().date().isoformat()

    # pfebook.com structure may change; here we try generic card/listing patterns
    for card in soup.select(".job-card, .card, article"):
        title_el = card.select_one("h2, h3, .job-title")
        title = title_el.get_text(strip=True) if title_el else "PFE opportunity"
        company_el = card.select_one(".company, .company-name, .job-company")
        company = company_el.get_text(strip=True) if company_el else ""
        desc_el = card.select_one(".description, .job-description, p")
        description = desc_el.get_text(" ", strip=True) if desc_el else ""
        link_el = card.select_one("a")
        href = link_el["href"] if link_el and link_el.has_attr("href") else url
        if href.startswith("/"):
            href = url.rstrip("/") + href
        email = ""
        items.append(
            ScrapeResult(
                title=title,
                company=company,
                link=href,
                description=description,
                contact_email=email,
                source_url=url,
                date_scraped=date,
            )
        )
    return items


def _scrape_hi_interns(url: str) -> List[ScrapeResult]:
    resp = _fetch(url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    items: List[ScrapeResult] = []
    date = dt.datetime.utcnow().date().isoformat()

    for card in soup.select(".internship-card, .card, article"):
        title_el = card.select_one("h2, h3, .title")
        title = title_el.get_text(strip=True) if title_el else "Internship / PFE"
        company_el = card.select_one(".company, .company-name")
        company = company_el.get_text(strip=True) if company_el else ""
        desc_el = card.select_one(".description, p")
        description = desc_el.get_text(" ", strip=True) if desc_el else ""
        link_el = card.select_one("a")
        href = link_el["href"] if link_el and link_el.has_attr("href") else url
        if href.startswith("/"):
            href = url.rstrip("/") + href
        items.append(
            ScrapeResult(
                title=title,
                company=company,
                link=href,
                description=description,
                contact_email="",
                source_url=url,
                date_scraped=date,
            )
        )
    return items


def _scrape_itgate(url: str) -> List[ScrapeResult]:
    resp = _fetch(url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    items: List[ScrapeResult] = []
    date = dt.datetime.utcnow().date().isoformat()

    for li in soup.select("li, .pfe-item, article"):
        text = li.get_text(" ", strip=True)
        if not text:
            continue
        title_el = li.select_one("h2, h3")
        title = title_el.get_text(strip=True) if title_el else text[:120]
        link_el = li.select_one("a")
        href = link_el["href"] if link_el and link_el.has_attr("href") else url
        if href.startswith("/"):
            href = url.rstrip("/") + href
        items.append(
            ScrapeResult(
                title=title,
                company="ITGate Group",
                link=href,
                description=text,
                contact_email="",
                source_url=url,
                date_scraped=date,
            )
        )
    return items


def _scrape_medianet(url: str) -> List[ScrapeResult]:
    resp = _fetch(url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    items: List[ScrapeResult] = []
    date = dt.datetime.utcnow().date().isoformat()

    for card in soup.select(".job-offer, .card, article"):
        title_el = card.select_one("h2, h3, .title")
        title = title_el.get_text(strip=True) if title_el else "Stage PFE"
        desc_el = card.select_one(".description, p")
        description = desc_el.get_text(" ", strip=True) if desc_el else ""
        link_el = card.select_one("a")
        href = link_el["href"] if link_el and link_el.has_attr("href") else url
        if href.startswith("/"):
            href = url.rstrip("/") + href
        items.append(
            ScrapeResult(
                title=title,
                company="Medianet",
                link=href,
                description=description,
                contact_email="",
                source_url=url,
                date_scraped=date,
            )
        )
    return items


def _scrape_generic(url: str) -> List[ScrapeResult]:
    resp = _fetch(url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    items: List[ScrapeResult] = []
    date = dt.datetime.utcnow().date().isoformat()

    # Very generic: each <a> containing 'PFE' or 'stage' is considered a project
    for a in soup.find_all("a"):
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        if "pfe" not in text.lower() and "stage" not in text.lower() and "projet" not in text.lower():
            continue
        href = a.get("href") or url
        if href.startswith("/"):
            href = url.rstrip("/") + href
        items.append(
            ScrapeResult(
                title=text[:120],
                company="",
                link=href,
                description=text,
                contact_email="",
                source_url=url,
                date_scraped=date,
            )
        )
    return items


def scrape_url(url: str) -> List[Dict[str, Any]]:
    """Dispatch to the appropriate scraper based on the domain.

    Returns a list of dicts ready to be turned into DataFrame rows.
    """

    logging.info("Scraping %s", url)
    lower = url.lower()
    if "pfebook.com" in lower or "pfebooks.com" in lower:
        items = _scrape_pfebook(url)
    elif "hi-interns.com" in lower:
        items = _scrape_hi_interns(url)
    elif "itgate-group.com" in lower:
        items = _scrape_itgate(url)
    elif "rh.medianet.tn" in lower:
        items = _scrape_medianet(url)
    else:
        items = _scrape_generic(url)

    logging.info("Found %d potential projects from %s", len(items), url)
    return [it.to_dict() for it in items]


__all__ = ["scrape_url"]

