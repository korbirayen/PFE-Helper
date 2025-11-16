"""Utility helpers for the PFE Aggregator bot.

This module centralizes filesystem paths, CSV helpers, fitness matching,
tracker handling, environment variable loading, and basic CV parsing.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from dotenv import load_dotenv
from slugify import slugify

try:  # pdfplumber is preferred
    import pdfplumber  # type: ignore
except Exception:  # pragma: no cover - optional dependency handling
    pdfplumber = None  # type: ignore

try:
    from PyPDF2 import PdfReader  # type: ignore
except Exception:  # pragma: no cover - optional dependency handling
    PdfReader = None  # type: ignore


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
EMAILS_DIR = ROOT_DIR / "emails"
MNT_DATA_DIR = ROOT_DIR / "mnt" / "data"


DEFAULT_COMPANIES_FILENAME = "PFE 2026 copy backup - pfeList.csv"
DEFAULT_PFEBOOK_PDF = "Opportunités stages PFE- Healio_Perspectives.pdf"
DEFAULT_CV_PDF = "Rayen Korbi.pdf"


def ensure_base_dirs() -> None:
    """Ensure that core folders used by the bot exist."""

    for d in (DATA_DIR, EMAILS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def detect_default_companies_csv() -> Path:
    """Return the path to the companies CSV.

    Preference order:
    1. /mnt/data/<DEFAULT_COMPANIES_FILENAME> if it exists
    2. data/companies.csv in the repo
    """

    mnt_path = MNT_DATA_DIR / DEFAULT_COMPANIES_FILENAME
    if mnt_path.exists():
        return mnt_path
    fallback = DATA_DIR / "companies.csv"
    return fallback


def detect_default_pfe_pdf() -> Optional[Path]:
    """Return default PFE book PDF path if found, else None."""

    pdf_path = MNT_DATA_DIR / DEFAULT_PFEBOOK_PDF
    return pdf_path if pdf_path.exists() else None


def detect_default_cv_pdf() -> Optional[Path]:
    """Return default CV PDF path if found, else None."""

    cv_path = MNT_DATA_DIR / DEFAULT_CV_PDF
    return cv_path if cv_path.exists() else None


def load_env() -> None:
    """Load environment variables from .env if present."""

    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def read_companies_csv(path: Optional[Path] = None) -> pd.DataFrame:
    """Load companies CSV with at least columns including 'Fitness Category'.

    The input file is expected to include a column for the company name
    (commonly something like 'Company' or 'company') and a 'Fitness Category'
    column with values such as High/Medium/Low.
    """

    if path is None:
        path = detect_default_companies_csv()

    if not path.exists():
        logging.warning("Companies CSV not found at %s; fitness matching will be limited.", path)
        return pd.DataFrame()

    df = pd.read_csv(path)
    # Normalize potential company column names
    lower_cols = {c.lower(): c for c in df.columns}
    company_col = None
    for candidate in ("company", "entreprise", "societe", "company name", "nom_societe"):
        if candidate in lower_cols:
            company_col = lower_cols[candidate]
            break

    if company_col is None:
        # Fall back to 'linkedin' source domain as pseudo company if necessary
        company_col = "company"
        if company_col not in df.columns:
            df[company_col] = ""

    df["__company_norm"] = df[company_col].astype(str).str.strip().str.lower()
    if "Fitness Category" in df.columns:
        df["Fitness Category"] = df["Fitness Category"].astype(str).str.strip()
    else:
        df["Fitness Category"] = ""

    return df


@dataclass
class FitnessMatch:
    company_csv: str
    fitness: str
    score: float
    approx: bool


def _tokenize_company(name: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


def match_company_fitness(company_name: str, companies_df: pd.DataFrame) -> Optional[FitnessMatch]:
    """Return best fitness match for a company using fuzzy token overlap.

    - Performs a simple token-based Jaccard similarity between the given name
      and each company in the CSV.
    - Returns None if the CSV is empty or all scores are 0.
    - Marks matches as approximate unless the normalized names are exactly equal.
    """

    if companies_df.empty:
        return None

    norm = company_name.strip().lower()
    if not norm:
        return None

    tokens_ref = set(_tokenize_company(norm))
    if not tokens_ref:
        return None

    best: Tuple[float, int] | None = None
    best_row: Optional[pd.Series] = None

    for idx, row in companies_df.iterrows():
        candidate = str(row.get("__company_norm", "")).strip()
        if not candidate:
            continue
        tokens_c = set(_tokenize_company(candidate))
        if not tokens_c:
            continue
        inter = len(tokens_ref & tokens_c)
        union = len(tokens_ref | tokens_c)
        score = inter / union if union else 0.0
        if best is None or score > best[0]:
            best = (score, idx)
            best_row = row

    if best is None or best[0] <= 0.0 or best_row is None:
        return None

    approx = best_row.get("__company_norm", "") != norm
    return FitnessMatch(
        company_csv=str(best_row.get("__company_norm", "")),
        fitness=str(best_row.get("Fitness Category", "")),
        score=float(best[0]),
        approx=approx,
    )


def make_project_id(title: str, company: str) -> str:
    """Create a stable slug ID from title and company."""

    base = f"{title} {company}".strip() or title or company
    return slugify(base)[:80]


TRACKER_COLUMNS = [
    "date_added",
    "project_id",
    "title",
    "company",
    "fitness",
    "pfe_link",
    "contact_email",
    "posted_telegram",
    "github_issue_url",
    "email_draft",
    "last_action",
    "status",
    "notes",
]


def tracker_path() -> Path:
    return DATA_DIR / "tracker.csv"


def ensure_tracker_exists() -> None:
    path = tracker_path()
    if path.exists():
        return
    ensure_base_dirs()
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(TRACKER_COLUMNS)


def append_tracker_row(row: Dict[str, Any]) -> None:
    """Append a row to tracker.csv, filling any missing columns with ''."""

    ensure_tracker_exists()
    path = tracker_path()
    ordered = [row.get(col, "") for col in TRACKER_COLUMNS]
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(ordered)


def update_tracker_status(project_id: str, status: str) -> bool:
    """Update status of a project in tracker.csv.

    Returns True if a row was updated.
    """

    path = tracker_path()
    if not path.exists():
        logging.warning("Tracker file not found at %s", path)
        return False

    rows: List[Dict[str, str]] = []
    updated = False
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or TRACKER_COLUMNS
        for r in reader:
            if r.get("project_id") == project_id:
                r["status"] = status
                r["last_action"] = dt.datetime.utcnow().isoformat()
                updated = True
            rows.append(r)

    if not updated:
        return False

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)

    return True


def load_tracker_index() -> Dict[str, Dict[str, str]]:
    """Load tracker.csv into a dict keyed by project_id.

    Returns an empty dict if the tracker does not exist yet.
    """

    path = tracker_path()
    if not path.exists():
        return {}

    index: Dict[str, Dict[str, str]] = {}
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            pid = r.get("project_id") or ""
            if pid:
                index[pid] = r
    return index


def update_tracker_field(project_id: str, field: str, value: str) -> bool:
    """Update a single field for a project in tracker.csv.

    Returns True if a row was updated.
    """

    path = tracker_path()
    if not path.exists():
        return False

    rows: List[Dict[str, str]] = []
    updated = False
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or TRACKER_COLUMNS
        for r in reader:
            if r.get("project_id") == project_id:
                r[field] = value
                r["last_action"] = dt.datetime.utcnow().isoformat()
                updated = True
            rows.append(r)

    if not updated:
        return False

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)

    return True


@dataclass
class ContactInfo:
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    raw_text_snippet: str | None = None

    def has_basic_fields(self) -> bool:
        return bool(self.name or self.email or self.phone)


def _extract_email(text: str) -> Optional[str]:
    m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return m.group(0) if m else None


def _extract_phone(text: str) -> Optional[str]:
    # Very loose Tunisian/international phone pattern
    m = re.search(r"\+?\d[\d\s]{7,15}", text)
    return m.group(0).strip() if m else None


def parse_cv_contact_info(cv_path: Optional[Path]) -> ContactInfo | None:
    """Parse minimal contact info from the CV PDF.

    If parsing fails, returns None. The caller can then insert placeholders
    in templates and print a warning for manual filling.
    """

    if cv_path is None or not cv_path.exists():
        logging.warning("CV PDF not found at %s; email drafts will use placeholders.", cv_path)
        return None

    text = ""
    try:
        if pdfplumber is not None:
            with pdfplumber.open(str(cv_path)) as pdf:  # type: ignore[arg-type]
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    text += "\n" + page_text
        elif PdfReader is not None:
            reader = PdfReader(str(cv_path))  # type: ignore[call-arg]
            for page in reader.pages:
                page_text = page.extract_text() or ""
                text += "\n" + page_text
    except Exception as exc:  # pragma: no cover - depends on actual file
        logging.warning("Failed to parse CV PDF at %s: %s", cv_path, exc)
        return None

    if not text.strip():
        return None

    email = _extract_email(text)
    phone = _extract_phone(text)

    # Very naive name heuristic: first line with at least two words and letters
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    name_candidate = None
    for line in lines[:10]:  # only scan first few lines
        if len(line.split()) >= 2 and re.search(r"[A-Za-z]", line):
            name_candidate = line
            break

    info = ContactInfo(name=name_candidate, email=email, phone=phone, raw_text_snippet="\n".join(lines[:20]))
    if not info.has_basic_fields():
        return None
    return info


def parse_fitness_filter(arg: Optional[str]) -> Optional[List[str]]:
    """Parse comma-separated fitness filter string into normalized list."""

    if not arg:
        return None
    return [x.strip() for x in arg.split(",") if x.strip()]


def parse_since_days(arg: Optional[int]) -> Optional[dt.datetime]:
    if not arg or arg <= 0:
        return None
    return dt.datetime.utcnow() - dt.timedelta(days=arg)


def parse_bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y"}


__all__ = [
    "ROOT_DIR",
    "DATA_DIR",
    "EMAILS_DIR",
    "MNT_DATA_DIR",
    "detect_default_companies_csv",
    "detect_default_pfe_pdf",
    "detect_default_cv_pdf",
    "load_env",
    "read_companies_csv",
    "match_company_fitness",
    "make_project_id",
    "FitnessMatch",
    "ContactInfo",
    "ensure_tracker_exists",
    "append_tracker_row",
    "update_tracker_status",
    "load_tracker_index",
    "update_tracker_field",
    "tracker_path",
    "parse_cv_contact_info",
    "parse_fitness_filter",
    "parse_since_days",
    "parse_bool_env",
]

