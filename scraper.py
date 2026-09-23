"""
scraper.py — Scrapes Fuzu, MyJobMag, and BrighterMonday for analyst/engineer jobs.

Each scraper returns a list of job dicts:
    {
        "title":       str,
        "company":     str,
        "location":    str,
        "url":         str,   # canonical, used as dedup key
        "date_posted": str,   # ISO 8601 or raw string; parsed best-effort
        "source":      str,   # "fuzu" | "myjobmag" | "brightermonday"
        "description": str,   # snippet / full text if available
    }
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urljoin, urlencode, quote

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Selenium (optional) ───────────────────────────────────────────────────────
# Some sources (Fuzu) only expose keyword-search results to a JavaScript-capable
# browser. When USE_SELENIUM is true we render those pages with headless Chrome;
# otherwise those scrapers fall back to their plain-HTTP behaviour.
USE_SELENIUM = os.getenv("USE_SELENIUM", "false").strip().lower() == "true"
SELENIUM_HEADLESS = os.getenv("SELENIUM_HEADLESS", "true").strip().lower() == "true"
SELENIUM_WAIT = 15   # seconds to wait for a results selector
SELENIUM_SETTLE = 3  # extra seconds for the results list to populate after load

# ── shared request settings ───────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 20  # seconds
RETRY_DELAY = 3       # seconds between retries
MAX_RETRIES = 2


def _get(url: str, params: dict | None = None) -> Optional[BeautifulSoup]:
    """GET with retries; returns BeautifulSoup or None on failure."""
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            resp = requests.get(
                url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as exc:
            logger.warning("Attempt %d for %s failed: %s", attempt, url, exc)
            if attempt <= MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    logger.error("All retries failed for %s", url)
    return None


# ── Headless-browser rendering (lazy, reused across a scrape_all run) ──────────

_DRIVER = None  # module-level singleton so 7 keywords share one browser


def _get_driver():
    """Create (once) and return a headless Chrome driver, or None if unavailable."""
    global _DRIVER
    if _DRIVER is not None:
        return _DRIVER
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        if SELENIUM_HEADLESS:
            opts.add_argument("--headless=new")
        for arg in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1400,2200",
            "--log-level=3",
            f"user-agent={HEADERS['User-Agent']}",
        ):
            opts.add_argument(arg)
        opts.add_experimental_option("excludeSwitches", ["enable-logging"])

        # Selenium Manager (bundled with Selenium ≥4.6) resolves the driver.
        _DRIVER = webdriver.Chrome(options=opts)
        _DRIVER.set_page_load_timeout(45)
        logger.info("Headless browser started for JS-rendered sources.")
        return _DRIVER
    except Exception as exc:
        logger.error(
            "Could not start headless Chrome (%s). Falling back to plain HTTP. "
            "Install Google Chrome, or set USE_SELENIUM=false to silence this.",
            exc,
        )
        _DRIVER = None
        return None


def _close_driver() -> None:
    """Quit the shared browser at the end of a scrape run."""
    global _DRIVER
    if _DRIVER is not None:
        try:
            _DRIVER.quit()
        except Exception:
            pass
        _DRIVER = None


def _get_rendered(url: str, wait_css: str) -> "BeautifulSoup | None":
    """
    Load a URL in headless Chrome, wait for `wait_css` to appear, and return
    the fully-rendered page as BeautifulSoup. Returns None on failure.
    """
    driver = _get_driver()
    if driver is None:
        return None
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver.get(url)
        try:
            WebDriverWait(driver, SELENIUM_WAIT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_css))
            )
        except Exception:
            logger.warning("Timed out waiting for '%s' at %s", wait_css, url)
        time.sleep(SELENIUM_SETTLE)
        return BeautifulSoup(driver.page_source, "lxml")
    except Exception as exc:
        logger.error("Headless render failed for %s: %s", url, exc)
        return None


def _parse_relative_date(text: str) -> str:
    """
    Convert relative strings like '2 days ago', 'Today', 'Yesterday' to ISO dates.
    Returns the original string unchanged if it cannot be parsed.
    """
    text = text.strip().lower()
    today = datetime.utcnow().date()
    if "today" in text or "just now" in text or "hour" in text:
        return today.isoformat()
    if "yesterday" in text:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s+day", text)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s+week", text)
    if m:
        return (today - timedelta(weeks=int(m.group(1)))).isoformat()
    m = re.search(r"(\d+)\s+month", text)
    if m:
        return (today - timedelta(days=int(m.group(1)) * 30)).isoformat()
    return text


# ── Fuzu ─────────────────────────────────────────────────────────────────────

FUZU_BASE = "https://www.fuzu.com"


def _scrape_fuzu_keyword(keyword: str) -> list[dict]:
    """
    Scrape one keyword from Fuzu Kenya jobs.

    Fuzu's keyword search runs client-side, so real filtering only happens in a
    JavaScript-capable browser. With USE_SELENIUM=true we load the search URL
    (`filters[term]=<keyword>`) in headless Chrome and the results appear in the
    <div class="b2c-card"> list. Without Selenium, Fuzu serves only its default
    (unfiltered) listing, which the local keyword filter then culls.
    """
    if USE_SELENIUM:
        url = (
            f"{FUZU_BASE}/kenya/job"
            f"?filters[country_id]=1&filters[term]={quote(keyword)}"
        )
        soup = _get_rendered(url, wait_css="div.b2c-card")
        # Fuzu intermittently rate-limits automated access and serves a page
        # with no job list; a single spaced retry usually recovers it.
        if soup is not None and not soup.select("div.b2c-card"):
            time.sleep(RETRY_DELAY)
            soup = _get_rendered(url, wait_css="div.b2c-card")
    else:
        url = f"{FUZU_BASE}/kenya/job"
        soup = _get(url)
    if soup is None:
        return []

    jobs: list[dict] = []

    # Each job is a <div class="b2c-card"> carrying structured data in
    # attributes (slug, location, company_slug, description, …).
    for card in soup.select("div.b2c-card"):
        try:
            title_el = card.select_one("h1, h2, h3, h4")
            title = title_el.get_text(strip=True) if title_el else ""
            slug = card.get("slug", "")
            if not title:
                title = slug.replace("-", " ").title() if slug else "Unknown"

            # Build the canonical job URL from the slug (a card's first <a> may
            # be a share/apply button); fall back to the first real link.
            if slug:
                job_url = urljoin(FUZU_BASE, f"/kenya/jobs/{slug}")
            else:
                link_el = card.select_one('a[href*="/kenya/jobs/"]')
                href = link_el.get("href", "") if link_el else ""
                job_url = urljoin(FUZU_BASE, href) if href else url

            company_slug = card.get("company_slug", "")
            company = company_slug.replace("-", " ").title() if company_slug else ""

            location = card.get("location") or "Nairobi, Kenya"

            # description is stored as HTML markup inside an attribute
            raw_desc = card.get("description", "")
            description = (
                BeautifulSoup(raw_desc, "lxml").get_text(" ", strip=True)[:500]
                if raw_desc else ""
            )

            jobs.append({
                "title": title,
                "company": company,
                "location": location,
                "url": job_url,
                "date_posted": datetime.utcnow().date().isoformat(),
                "source": "fuzu",
                "description": description,
            })
        except Exception as exc:
            logger.debug("Error parsing Fuzu card: %s", exc)

    return jobs


def scrape_fuzu(keywords: list[str]) -> list[dict]:
    logger.info("Scraping Fuzu for %d keyword(s)…", len(keywords))
    results: list[dict] = []
    for kw in keywords:
        found = _scrape_fuzu_keyword(kw)
        logger.info("  Fuzu '%s' → %d jobs", kw, len(found))
        results.extend(found)
        time.sleep(1)
    return results


# ── MyJobMag ─────────────────────────────────────────────────────────────────

MYJOBMAG_BASE = "https://www.myjobmag.co.ke"


def _scrape_myjobmag_keyword(keyword: str, location: str = "nairobi") -> list[dict]:
    # MyJobMag's search is server-rendered and genuinely filters by keyword.
    url = f"{MYJOBMAG_BASE}/search"
    soup = _get(url, params={"q": keyword})
    if soup is None:
        return []

    jobs: list[dict] = []

    # Each result is <li class="job-info"> containing an <h2><a href="/job/…">.
    cards = [
        li for li in soup.select("li.job-info")
        if li.select_one('h2 a[href*="/job/"]')
    ]

    for card in cards:
        try:
            link_el = card.select_one('h2 a[href*="/job/"]')
            raw_title = link_el.get_text(strip=True)
            href = link_el.get("href", "")
            job_url = urljoin(MYJOBMAG_BASE, href) if href else url

            # Titles are formatted "<Role> at <Company>".
            if " at " in raw_title:
                title, company = raw_title.rsplit(" at ", 1)
            else:
                title, company = raw_title, ""

            desc_el = card.select_one("li.job-desc")
            description = desc_el.get_text(" ", strip=True)[:500] if desc_el else ""

            date_el = card.select_one("#job-date")
            # Absolute date such as "11 September" — keep as-is (the filter
            # treats unparseable dates as recent rather than dropping them).
            date_posted = (
                date_el.get_text(strip=True) if date_el
                else datetime.utcnow().date().isoformat()
            )

            jobs.append({
                "title": title.strip(),
                "company": company.strip(),
                "location": location.title(),
                "url": job_url,
                "date_posted": date_posted,
                "source": "myjobmag",
                "description": description,
            })
        except Exception as exc:
            logger.debug("Error parsing MyJobMag card: %s", exc)

    return jobs


def scrape_myjobmag(keywords: list[str], location: str = "nairobi") -> list[dict]:
    logger.info("Scraping MyJobMag for %d keyword(s)…", len(keywords))
    results: list[dict] = []
    for kw in keywords:
        found = _scrape_myjobmag_keyword(kw, location)
        logger.info("  MyJobMag '%s' → %d jobs", kw, len(found))
        results.extend(found)
        time.sleep(1)
    return results


# ── BrighterMonday ────────────────────────────────────────────────────────────

BRIGHTERMONDAY_BASE = "https://www.brightermonday.co.ke"


def _scrape_brightermonday_keyword(keyword: str, location: str = "nairobi") -> list[dict]:
    """
    Scrape BrighterMonday.

    BrighterMonday sits behind Cloudflare bot protection: only a couple of
    featured listing cards are exposed to automated clients (plain HTTP *and*
    headless Chrome both receive the same reduced page), so this scraper stays
    on plain HTTP and extracts what is available. Retrieving the full result
    list would require anti-bot evasion, which is out of scope.
    """
    url = f"{BRIGHTERMONDAY_BASE}/jobs"
    soup = _get(url, params={"q": keyword})
    if soup is None:
        return []

    jobs: list[dict] = []

    for card in soup.select('div[data-cy="listing-cards-components"]'):
        try:
            title_el = (
                card.select_one('[data-cy="listing-title-link"]')
                or card.select_one('a[href*="/listings/"]')
            )
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            link_el = card.select_one('a[href*="/listings/"]')
            href = link_el.get("href", "") if link_el else ""
            job_url = urljoin(BRIGHTERMONDAY_BASE, href) if href else url

            company_el = card.select_one('a[href*="/company/"]')
            company = company_el.get_text(strip=True) if company_el else ""

            # Best-effort relative date pulled from the card text ("5 days ago").
            card_text = card.get_text(" ", strip=True)
            m = re.search(
                r"(today|yesterday|\d+\s+(?:day|week|month)s?\s+ago)",
                card_text, re.I,
            )
            date_posted = (
                _parse_relative_date(m.group(1)) if m
                else datetime.utcnow().date().isoformat()
            )

            jobs.append({
                "title": title,
                "company": company,
                "location": location.title(),
                "url": job_url,
                "date_posted": date_posted,
                "source": "brightermonday",
                "description": "",
            })
        except Exception as exc:
            logger.debug("Error parsing BrighterMonday card: %s", exc)

    return jobs


def scrape_brightermonday(keywords: list[str], location: str = "nairobi") -> list[dict]:
    logger.info("Scraping BrighterMonday for %d keyword(s)…", len(keywords))
    results: list[dict] = []
    for kw in keywords:
        found = _scrape_brightermonday_keyword(kw, location)
        logger.info("  BrighterMonday '%s' → %d jobs", kw, len(found))
        results.extend(found)
        time.sleep(1)
    return results


# ── Public entry point ────────────────────────────────────────────────────────

def scrape_all(
    sources: list[str],
    keywords: list[str],
    location: str = "nairobi",
) -> list[dict]:
    """
    Scrape all enabled sources.

    Args:
        sources:  list of source names, e.g. ["fuzu", "myjobmag", "brightermonday"]
        keywords: role keyword strings
        location: city filter passed to each scraper
    """
    all_jobs: list[dict] = []
    source_set = {s.strip().lower() for s in sources}

    try:
        if "fuzu" in source_set:
            all_jobs.extend(scrape_fuzu(keywords))
        if "myjobmag" in source_set:
            all_jobs.extend(scrape_myjobmag(keywords, location))
        if "brightermonday" in source_set:
            all_jobs.extend(scrape_brightermonday(keywords, location))
    finally:
        _close_driver()  # release the headless browser if one was started

    logger.info("Total scraped (pre-filter): %d jobs", len(all_jobs))
    return all_jobs
