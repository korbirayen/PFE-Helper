"""Main CLI entrypoint for the PFE Aggregator bot.

Usage examples (once dependencies are installed and .env is configured):

    python scripts/aggregator_bot.py --top 30 --fitness High,Medium --generate-emails --save-csv
    python scripts/aggregator_bot.py --post-telegram --fitness High --since-days 7
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from . import scrapers
from .parse_pdf import extract_pfe_entries_from_pdf
from .templates import render_email, render_issue
from .utils import (
    DATA_DIR,
    EMAILS_DIR,
    ContactInfo,
    append_tracker_row,
    detect_default_companies_csv,
    detect_default_cv_pdf,
    detect_default_pfe_pdf,
    ensure_tracker_exists,
    load_env,
    make_project_id,
    match_company_fitness,
    parse_cv_contact_info,
    parse_fitness_filter,
    parse_since_days,
    read_companies_csv,
    tracker_path,
    update_tracker_status,
)


SCRAPE_SOURCES = [
    "https://www.pfebook.com/",
    "https://hi-interns.com/internships",
    "https://itgate-group.com/catalogue-pfe/",
    "https://rh.medianet.tn/Fr/stages-pfe-2026_11_50",
    "https://pfebooks.com/",
]


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Aggregate PFE projects from web + PDFs and generate outputs.")
    p.add_argument("--top", type=int, default=None, help="Limit number of projects to process/output.")
    p.add_argument("--fitness", type=str, default=None, help="Comma-separated fitness levels to keep, e.g. 'High,Medium'.")
    p.add_argument("--since-days", type=int, default=None, help="Only include projects scraped in the last N days.")
    p.add_argument("--post-telegram", action="store_true", help="Post selected projects to Telegram.")
    p.add_argument("--create-issues", action="store_true", help="Create GitHub issues for selected projects.")
    p.add_argument("--generate-emails", action="store_true", help="Generate email drafts in emails/ folder.")
    p.add_argument("--save-csv", action="store_true", help="Save aggregated projects to data/aggregated_projects.csv.")
    p.add_argument("--force", action="store_true", help="Force overwriting aggregated_projects.csv if it exists.")
    p.add_argument("--debug", action="store_true", help="Enable debug logging.")
    p.add_argument("--update-status", type=str, default=None, help="Update tracker status for a given project_id, e.g. 'my-id:Contacted'.")
    return p


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")


def load_sources() -> pd.DataFrame:
    """Scrape configured web sources and parse default PFE PDF if available."""

    all_entries: List[Dict[str, Any]] = []

    # Web sources
    for url in SCRAPE_SOURCES:
        try:
            entries = scrapers.scrape_url(url)
            all_entries.extend(entries)
        except Exception as exc:  # pragma: no cover - network dependent
            logging.warning("Error scraping %s: %s", url, exc)

    # PDF source
    pdf_path = detect_default_pfe_pdf()
    if pdf_path is not None:
        try:
            pdf_entries = extract_pfe_entries_from_pdf(pdf_path)
            all_entries.extend(pdf_entries)
        except Exception as exc:  # pragma: no cover
            logging.warning("Error parsing PFE PDF %s: %s", pdf_path, exc)
    else:
        logging.info("No default PFE PDF found; skipping PDF parsing.")

    if not all_entries:
        logging.warning("No projects found from sources.")
        return pd.DataFrame(columns=[
            "title",
            "company",
            "link",
            "description",
            "contact_email",
            "source_url",
            "date_scraped",
        ])

    df = pd.DataFrame(all_entries)
    # Normalize columns
    df["title"] = df["title"].fillna("").astype(str).str.strip()
    df["company"] = df["company"].fillna("").astype(str).str.strip()
    df["description"] = df["description"].fillna("").astype(str)
    df["contact_email"] = df["contact_email"].fillna("").astype(str)
    df["source_url"] = df["source_url"].fillna("").astype(str)
    df["date_scraped"] = df["date_scraped"].fillna(dt.date.today().isoformat()).astype(str)
    return df


def apply_fitness(df: pd.DataFrame, companies_df: pd.DataFrame) -> pd.DataFrame:
    """Match each project with a fitness score based on company."""

    fitness_vals: List[str] = []
    match_company_vals: List[str] = []
    approx_flags: List[bool] = []
    scores: List[float] = []

    for _, row in df.iterrows():
        m = match_company_fitness(str(row.get("company", "")), companies_df)
        if m is None:
            fitness_vals.append("")
            match_company_vals.append("")
            approx_flags.append(False)
            scores.append(0.0)
        else:
            fitness_vals.append(m.fitness)
            match_company_vals.append(m.company_csv)
            approx_flags.append(m.approx)
            scores.append(m.score)

    df = df.copy()
    df["fitness"] = fitness_vals
    df["csv_company_match"] = match_company_vals
    df["fitness_match_approx"] = approx_flags
    df["fitness_match_score"] = scores
    return df


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Merge duplicates based on (title, company) keeping the best record.

    The "best" record is the one with the highest fitness_match_score.
    """

    if df.empty:
        return df

    df = df.copy()
    # Create a slug project id
    df["project_id"] = [make_project_id(t, c) for t, c in zip(df["title"], df["company"])]
    df.sort_values("fitness_match_score", ascending=False, inplace=True)
    df = df.drop_duplicates(subset=["title", "company"], keep="first")
    return df


def filter_by_fitness_and_date(
    df: pd.DataFrame,
    fitness_filter: Optional[List[str]] = None,
    since: Optional[dt.datetime] = None,
) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()
    if fitness_filter:
        df = df[df["fitness"].isin(fitness_filter)]

    if since:
        # date_scraped is ISO date; we parse to datetime
        def parse_date(value: str) -> Optional[dt.datetime]:
            try:
                return dt.datetime.fromisoformat(value)
            except Exception:
                try:
                    return dt.datetime.strptime(value, "%Y-%m-%d")
                except Exception:
                    return None

        mask = []
        for v in df["date_scraped"].astype(str):
            d = parse_date(v)
            mask.append(d is None or d >= since)
        df = df[mask]

    return df


def load_my_contact_info() -> Dict[str, str]:
    """Load contact info from CV PDF or fallback to placeholders."""

    cv_path = detect_default_cv_pdf()
    info: Optional[ContactInfo] = parse_cv_contact_info(cv_path)
    if info is None:
        logging.warning(
            "Could not extract contact info from CV; using placeholders {MY_NAME}, {MY_EMAIL}, {MY_PHONE}."
        )
        return {"my_name": "{MY_NAME}", "my_email": "{MY_EMAIL}", "my_phone": "{MY_PHONE}"}

    return {
        "my_name": info.name or "{MY_NAME}",
        "my_email": info.email or "{MY_EMAIL}",
        "my_phone": info.phone or "{MY_PHONE}",
    }


def generate_email_drafts(df: pd.DataFrame, my_info: Dict[str, str]) -> int:
    EMAILS_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    today = dt.date.today().isoformat()
    for _, row in df.iterrows():
        project = row.to_dict()
        body_fr = render_email(project, my_info, language="fr")
        body_en = render_email(project, my_info, language="en")
        slug = row.get("project_id") or make_project_id(project.get("title", ""), project.get("company", ""))
        filename = f"{today}_{slug}.txt"
        path = EMAILS_DIR / filename
        with path.open("w", encoding="utf-8") as f:
            f.write("# French version\n\n")
            f.write(body_fr)
            f.write("\n\n# English version\n\n")
            f.write(body_en)
        count += 1
    return count


def post_to_telegram(df: pd.DataFrame) -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logging.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; cannot post to Telegram.")
        return 0

    base_url = f"https://api.telegram.org/bot{token}/sendMessage"
    count = 0
    for _, row in df.iterrows():
        text = f"PFE: {row['title']} — {row['company'] or 'N/A'}\nFitness: {row['fitness'] or 'N/A'}"\
            f"{' (approx company match)' if row.get('fitness_match_approx') else ''}\n"\
            f"Link: {row['link'] or row['source_url']}"
        payload = {"chat_id": chat_id, "text": text}
        try:
            resp = requests.post(base_url, json=payload, timeout=15)
            if resp.status_code >= 400:
                logging.warning("Telegram API error %s: %s", resp.status_code, resp.text)
            else:
                count += 1
        except Exception as exc:  # pragma: no cover
            logging.warning("Error posting to Telegram: %s", exc)
    return count


def create_github_issues(df: pd.DataFrame) -> int:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")
    if not token or not repo:
        logging.error("GITHUB_TOKEN or GITHUB_REPO not set; cannot create GitHub issues.")
        return 0

    base_url = f"https://api.github.com/repos/{repo}/issues"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    count = 0
    for _, row in df.iterrows():
        title = f"PFE: {row['title']} — {row['company'] or 'N/A'}"
        body = render_issue(row.to_dict())
        payload = {"title": title, "body": body}
        try:
            resp = requests.post(base_url, json=payload, headers=headers, timeout=15)
            if resp.status_code >= 400:
                logging.warning("GitHub API error %s: %s", resp.status_code, resp.text)
            else:
                count += 1
        except Exception as exc:  # pragma: no cover
            logging.warning("Error creating GitHub issue: %s", exc)
    return count


def save_aggregated_csv(df: pd.DataFrame, force: bool = False) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "aggregated_projects.csv"
    if path.exists() and not force:
        logging.info("aggregated_projects.csv already exists; use --force to overwrite.")
        return path
    df.to_csv(path, index=False)
    return path


def update_tracker(df: pd.DataFrame) -> None:
    ensure_tracker_exists()
    now = dt.datetime.utcnow().isoformat()
    for _, row in df.iterrows():
        append_tracker_row(
            {
                "date_added": now,
                "project_id": row.get("project_id", ""),
                "title": row.get("title", ""),
                "company": row.get("company", ""),
                "fitness": row.get("fitness", ""),
                "pfe_link": row.get("link", ""),
                "contact_email": row.get("contact_email", ""),
                "posted_telegram": "",
                "github_issue_url": "",
                "email_draft": "",
                "last_action": now,
                "status": "new",
                "notes": "fitness_match_approx="
                + ("True" if row.get("fitness_match_approx") else "False"),
            }
        )


def main(argv: Optional[List[str]] = None) -> int:
    load_env()
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    setup_logging(args.debug)

    if args.update_status:
        if ":" not in args.update_status:
            logging.error("--update-status expects format 'project_id:new_status'")
            return 1
        pid, status = args.update_status.split(":", 1)
        ok = update_tracker_status(pid, status)
        if ok:
            logging.info("Updated status for %s to %s", pid, status)
            return 0
        logging.error("No tracker row found for project_id=%s", pid)
        return 1

    companies_df = read_companies_csv(detect_default_companies_csv())
    projects_df = load_sources()
    if projects_df.empty:
        logging.error("No projects collected; exiting.")
        return 1

    projects_df = apply_fitness(projects_df, companies_df)
    projects_df = deduplicate(projects_df)

    fitness_filter = parse_fitness_filter(args.fitness)
    since = parse_since_days(args.since_days)
    projects_df = filter_by_fitness_and_date(projects_df, fitness_filter, since)

    if args.top is not None and args.top > 0:
        projects_df = projects_df.head(args.top)

    if projects_df.empty:
        logging.warning("No projects left after filtering; nothing to do.")
        return 0

    # Update tracker first with base info
    update_tracker(projects_df)

    my_info = load_my_contact_info()

    count_emails = count_telegram = count_issues = 0
    if args.generate_emails:
        count_emails = generate_email_drafts(projects_df, my_info)

    if args.post_telegram:
        count_telegram = post_to_telegram(projects_df)

    if args.create_issues:
        count_issues = create_github_issues(projects_df)

    csv_path = None
    if args.save_csv:
        csv_path = save_aggregated_csv(projects_df, force=args.force)

    # Summary
    logging.info("Processed %d projects.", len(projects_df))
    logging.info("Email drafts generated: %d", count_emails)
    logging.info("Telegram posts sent: %d", count_telegram)
    logging.info("GitHub issues created: %d", count_issues)
    if csv_path:
        logging.info("Aggregated CSV saved to: %s", csv_path)
    logging.info("Tracker path: %s", tracker_path())

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

