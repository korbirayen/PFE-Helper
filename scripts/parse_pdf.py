"""PDF parsing utilities for extracting PFE projects and CV data."""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

try:
    import pdfplumber  # type: ignore
except Exception:  # pragma: no cover - optional
    pdfplumber = None  # type: ignore

try:
    from PyPDF2 import PdfReader  # type: ignore
except Exception:  # pragma: no cover - optional
    PdfReader = None  # type: ignore


KEYWORDS = ["pfe", "projet", "stage"]


def _read_pdf_text(path: Path) -> str:
    """Read all text from PDF using pdfplumber, falling back to PyPDF2."""

    text = ""
    if pdfplumber is not None:
        try:
            with pdfplumber.open(str(path)) as pdf:  # type: ignore[arg-type]
                for page in pdf.pages:
                    page_text = page.extract_text() or ""
                    text += "\n" + page_text
        except Exception as exc:  # pragma: no cover
            logging.warning("pdfplumber failed for %s: %s", path, exc)

    if not text and PdfReader is not None:
        try:
            reader = PdfReader(str(path))  # type: ignore[call-arg]
            for page in reader.pages:
                page_text = page.extract_text() or ""
                text += "\n" + page_text
        except Exception as exc:  # pragma: no cover
            logging.warning("PyPDF2 failed for %s: %s", path, exc)

    return text


def extract_pfe_entries_from_pdf(path: Path) -> List[Dict[str, Any]]:
    """Extract candidate PFE projects from a PDF.

    We use a simple heuristic: any line containing PFE-related keywords is
    considered part of a project description. Consecutive matching lines are
    grouped as a single project entry.
    """

    if not path.exists():
        logging.warning("PFE PDF not found at %s", path)
        return []

    text = _read_pdf_text(path)
    if not text.strip():
        logging.warning("No text extracted from PFE PDF %s", path)
        return []

    lines = [l.strip() for l in text.splitlines()]
    entries: List[Dict[str, Any]] = []
    buf: List[str] = []
    date = dt.datetime.utcnow().date().isoformat()

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        block = " ".join(buf)
        # Rough title as first sentence or first 120 chars
        title = block.split(".")[0][:150]
        entries.append(
            {
                "title": title or "Projet PFE",
                "company": "",
                "link": "",
                "description": block,
                "contact_email": "",
                "source_url": str(path),
                "date_scraped": date,
            }
        )
        buf = []

    for line in lines:
        low = line.lower()
        if any(k in low for k in KEYWORDS):
            buf.append(line)
        else:
            flush()

    flush()
    logging.info("Extracted %d PFE-like entries from %s", len(entries), path)
    return entries


__all__ = ["extract_pfe_entries_from_pdf"]

