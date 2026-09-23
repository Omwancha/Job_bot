"""
filter.py — Deduplicates jobs using SQLite, then filters by date and keywords.

Flow:
    1. Initialise (or open) the SQLite DB at DB_PATH.
    2. For each scraped job, skip if URL already in `seen_jobs`.
    3. Discard jobs older than MAX_AGE_DAYS.
    4. Discard jobs whose title/description/company match no keyword from profile.json.
    5. Insert surviving jobs into `seen_jobs` so they are never re-notified.
    6. Return the surviving new jobs.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, date

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "./jobbot.db")
MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "7"))


# ── DB setup ──────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the seen_jobs table if it doesn't exist."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_jobs (
                url          TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                company      TEXT,
                location     TEXT,
                source       TEXT,
                date_posted  TEXT,
                first_seen   TEXT NOT NULL
            )
        """)
        conn.commit()
    logger.debug("DB initialised at %s", DB_PATH)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> date | None:
    """
    Parse a date string to a date, or return None if unparseable.

    Handles ISO and common absolute formats, plus year-less formats such as
    "11 September" (as produced by MyJobMag). A year-less date is assumed to be
    its most recent occurrence: the current year, or the previous year if that
    would place it in the future (handles the December/January boundary).
    """
    if not raw:
        return None
    raw = raw.strip()

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except (ValueError, AttributeError):
            pass

    today = datetime.utcnow().date()
    for fmt in ("%d %B", "%d %b", "%B %d", "%b %d"):
        try:
            parsed = datetime.strptime(raw, fmt).date().replace(year=today.year)
        except (ValueError, AttributeError):
            continue
        if parsed > today:
            try:
                parsed = parsed.replace(year=today.year - 1)
            except ValueError:  # e.g. 29 Feb in a non-leap previous year
                return None
        return parsed

    return None


def _is_recent(date_str: str, max_age: int) -> bool:
    """Return True if the job's date is within max_age days of today."""
    parsed = _parse_date(date_str)
    if parsed is None:
        # Can't determine age — include it to avoid silently dropping jobs
        return True
    cutoff = datetime.utcnow().date() - timedelta(days=max_age)
    return parsed >= cutoff


def _load_keywords(profile_path: str = "./profile.json") -> list[str]:
    """Load keyword list from profile.json (target_roles.keywords + titles)."""
    try:
        with open(profile_path, encoding="utf-8") as f:
            profile = json.load(f)
        roles = profile.get("target_roles", {})
        keywords: list[str] = []
        keywords.extend(roles.get("keywords", []))
        keywords.extend(roles.get("titles", []))
        return [k.lower() for k in keywords if k]
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Could not load profile.json keywords: %s", exc)
        return []


def _matches_keywords(job: dict, keywords: list[str]) -> bool:
    """Return True if any keyword appears in title, description, or company."""
    if not keywords:
        return True  # No filter configured — pass everything
    haystack = " ".join([
        job.get("title", ""),
        job.get("description", ""),
        job.get("company", ""),
        job.get("location", ""),
    ]).lower()
    return any(kw in haystack for kw in keywords)


def _is_new(url: str, conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM seen_jobs WHERE url = ?", (url,)).fetchone()
    return row is None


def _mark_seen(job: dict, conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO seen_jobs
            (url, title, company, location, source, date_posted, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job["url"],
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            job.get("source", ""),
            job.get("date_posted", ""),
            datetime.utcnow().date().isoformat(),
        ),
    )


# ── Public entry point ────────────────────────────────────────────────────────

def filter_jobs(
    raw_jobs: list[dict],
    profile_path: str = "./profile.json",
    max_age_days: int | None = None,
) -> list[dict]:
    """
    Deduplicate and filter scraped jobs.

    Args:
        raw_jobs:     Output of scraper.scrape_all()
        profile_path: Path to profile.json
        max_age_days: Override MAX_AGE_DAYS env var

    Returns:
        List of new, relevant jobs (also persisted to DB as seen).
    """
    init_db()
    age_limit = max_age_days if max_age_days is not None else MAX_AGE_DAYS
    keywords = _load_keywords(profile_path)
    logger.info(
        "Filtering %d raw jobs | max_age=%d days | %d keywords",
        len(raw_jobs), age_limit, len(keywords),
    )

    new_jobs: list[dict] = []

    with _get_conn() as conn:
        for job in raw_jobs:
            url = job.get("url", "").strip()
            if not url:
                continue

            if not _is_new(url, conn):
                logger.debug("Skip (seen):  %s", url)
                continue

            if not _is_recent(job.get("date_posted", ""), age_limit):
                logger.debug("Skip (old):   %s", url)
                _mark_seen(job, conn)  # still mark to avoid re-checking
                continue

            if not _matches_keywords(job, keywords):
                logger.debug("Skip (kw):    %s | %s", job.get("title"), url)
                continue

            _mark_seen(job, conn)
            new_jobs.append(job)

        conn.commit()

    logger.info("New relevant jobs after filtering: %d", len(new_jobs))
    return new_jobs
