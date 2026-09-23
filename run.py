"""
run.py — Entry point for JobBot.

Schedules the full pipeline once daily at the time set in RUN_TIME (.env).
Can also be run immediately with:  python run.py --now

Pipeline:
    1. scraper.scrape_all()   — fetch raw jobs from enabled sources
    2. filter.filter_jobs()   — deduplicate, date-check, keyword-match
    3. notifier.send_digest() — send Gmail SMTP digest (always)
    4. applier.apply_to_jobs()— send tailored applications (only if auto_apply_enabled)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

import schedule
import time

from dotenv import load_dotenv

load_dotenv()

# ── Ensure UTF-8 console output ───────────────────────────────────────────────
# Log messages contain non-ASCII characters (—, →, …). On Windows the console
# defaults to cp1252, which raises UnicodeEncodeError on every such message.
# Reconfigure stdout/stderr to UTF-8 before any handler wraps them.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# ── Logging setup (do this before importing project modules) ──────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("jobbot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("jobbot.run")

# ── Import project modules ────────────────────────────────────────────────────
from scraper import scrape_all
from filter import filter_jobs
from notifier import send_digest
from applier import apply_to_jobs

# ── Config from .env ──────────────────────────────────────────────────────────
RUN_TIME = os.getenv("RUN_TIME", "08:00").strip()
SCRAPE_SOURCES = [s.strip() for s in os.getenv("SCRAPE_SOURCES", "fuzu,myjobmag,brightermonday").split(",")]
ROLE_KEYWORDS = [k.strip() for k in os.getenv("ROLE_KEYWORDS", "data analyst,analytics engineer").split(",")]
LOCATION = os.getenv("LOCATION", "Nairobi").strip()


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline() -> None:
    """Execute the full scrape → filter → notify → apply pipeline."""
    start = datetime.now()
    logger.info("=" * 60)
    logger.info("JobBot pipeline starting at %s", start.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Sources  : %s", ", ".join(SCRAPE_SOURCES))
    logger.info("Keywords : %s", ", ".join(ROLE_KEYWORDS))
    logger.info("Location : %s", LOCATION)
    logger.info("=" * 60)

    # 1. Scrape
    try:
        raw_jobs = scrape_all(
            sources=SCRAPE_SOURCES,
            keywords=ROLE_KEYWORDS,
            location=LOCATION,
        )
    except Exception:
        logger.exception("Scraper failed — aborting pipeline.")
        return

    # 2. Filter
    try:
        new_jobs = filter_jobs(raw_jobs)
    except Exception:
        logger.exception("Filter failed — aborting pipeline.")
        return

    logger.info("%d new relevant jobs after filtering.", len(new_jobs))

    # 3. Notify (always — even if 0 jobs, so you know the bot ran)
    try:
        sent = send_digest(new_jobs)
        if not sent:
            logger.warning("Digest email was not sent (check SMTP config).")
    except Exception:
        logger.exception("Notifier failed.")

    # 4. Auto-apply (only if enabled in profile.json)
    try:
        apply_to_jobs(new_jobs)
    except Exception:
        logger.exception("Applier failed.")

    elapsed = (datetime.now() - start).total_seconds()
    logger.info("Pipeline complete in %.1fs.", elapsed)
    logger.info("=" * 60)


# ── Scheduler ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JobBot — daily job scraper and notifier")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Run the pipeline immediately instead of waiting for the scheduled time.",
    )
    args = parser.parse_args()

    if args.now:
        logger.info("--now flag set: running pipeline immediately.")
        run_pipeline()
        return

    logger.info("JobBot scheduler started. Pipeline will run daily at %s.", RUN_TIME)
    logger.info("Use  python run.py --now  to trigger an immediate run.")
    logger.info("Press Ctrl+C to stop.")

    schedule.every().day.at(RUN_TIME).do(run_pipeline)

    # Run immediately on first start so you don't wait until tomorrow
    logger.info("Running initial pipeline on startup…")
    run_pipeline()

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("JobBot stopped by user.")


if __name__ == "__main__":
    main()
