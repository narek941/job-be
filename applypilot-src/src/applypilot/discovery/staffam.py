"""staff.am discovery — list pages via HTTP (no public API).

Parses job listing HTML for https://staff.am/en/jobs (and optional search query).
Designed for Armenian market alongside JobSpy/LinkedIn.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from applypilot import config
from applypilot.database import get_connection, init_db, store_jobs

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
SITE_LABEL = "staff.am"
LIST_BASE = "https://staff.am/en/jobs"


def _normalize_url(href: str) -> str | None:
    if not href or href.startswith("#"):
        return None
    full = urljoin(LIST_BASE, href)
    parsed = urlparse(full)
    if "staff.am" not in parsed.netloc:
        return None
    path = parsed.path or ""
    if "/company/" in path:
        return None
    if not re.search(r"/(?:en|hy)/jobs/[^/]+/.+", path):
        return None
    return f"{parsed.scheme}://{parsed.netloc}{path.split('?')[0]}"


def _parse_listing_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    seen: set[str] = set()

    for a in soup.select('a[href*="/jobs/"]'):
        href = a.get("href") or ""
        url = _normalize_url(href)
        if not url or url in seen:
            continue
        title = (a.get_text() or "").strip()
        if not title or len(title) < 3:
            continue
        seen.add(url)

        company = ""
        card = a.find_parent(["article", "div", "li"])
        if card:
            for link in card.select('a[href*="/company/"]'):
                company = (link.get_text() or "").strip()
                if company:
                    break

        loc = ""
        if card:
            text = card.get_text(" ", strip=True)
            for city in ("Yerevan", "Gyumri", "Vanadzor", "Remote", "Հեռավար"):
                if city.lower() in text.lower():
                    loc = city if city != "Հեռավար" else "Remote"
                    break

        jobs.append(
            {
                "url": url,
                "title": title,
                "company": company,
                "salary": None,
                "description": None,
                "location": loc or "Armenia",
            }
        )

    return jobs


def extract_email(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    return match.group(0) if match else None


def scrape_staffam_job_details(url: str) -> dict:
    """Fetch and parse full job details from a staff.am page."""
    try:
        with httpx.Client(timeout=45.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
    except Exception as e:
        log.error("staff.am detail fetch failed (%s): %s", url, e)
        return {}

    soup = BeautifulSoup(r.text, "html.parser")
    
    # Description logic
    # Staff.am details are usually in a container with headers like "Job responsibilities"
    desc_box = soup.select_one(".job-list-detail") or soup.select_one(".job-description")
    if not desc_box:
        # Fallback to main content container if possible
        desc_box = soup.select_one(".job-details-info")

    full_text = ""
    if desc_box:
        full_text = desc_box.get_text("\n", strip=True)

    # Application logic
    apply_url = None
    apply_btn = soup.select_one('a[href*="/add-cv-submits"]')
    if apply_btn:
        href = apply_btn.get("href", "")
        # Check for originUrl redirection which points to external portals (Workday, etc)
        if "originUrl=" in href:
            match = re.search(r"originUrl=([^&]+)", href)
            if match:
                from urllib.parse import unquote
                apply_url = unquote(match.group(1))
        
        if not apply_url:
             apply_url = urljoin(LIST_BASE, href)

    # Email detection
    email = extract_email(full_text)
    
    return {
        "full_description": full_text,
        "application_url": apply_url,
        "application_email": email
    }


def fetch_staffam_page(search_query: str | None = None, page: int = 1) -> list[dict]:
    """Fetch one listing page; optional search query."""
    params: dict[str, str] = {}
    if search_query:
        params["search"] = search_query
    if page > 1:
        params["page"] = str(page)

    url = LIST_BASE
    try:
        with httpx.Client(timeout=45.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
            r = client.get(url, params=params or None)
            r.raise_for_status()
    except Exception as e:
        log.error("staff.am fetch failed: %s", e)
        return []

    return _parse_listing_html(r.text)


def run_staffam_discovery(
    keywords: list[str] | None = None,
    max_pages_per_keyword: int = 2,
) -> dict:
    """Discover jobs on staff.am and store in ApplyPilot DB.

    Keywords default to search config ``queries`` entries plus optional
    ``staff_am.extra_keywords``.
    """
    init_db()
    cfg = config.load_search_config() or {}

    if keywords is None:
        keywords = []
        for q in cfg.get("queries", []):
            if isinstance(q, dict) and q.get("query"):
                keywords.append(str(q["query"]))
        extra = (cfg.get("staff_am") or {}).get("extra_keywords") or []
        keywords.extend(str(x) for x in extra)

    if not keywords:
        keywords = [""]

    seen_urls: set[str] = set()
    all_jobs: list[dict] = []

    for kw in keywords:
        for page in range(1, max_pages_per_keyword + 1):
            q = kw if kw else None
            batch = fetch_staffam_page(search_query=q, page=page)
            if not batch:
                break
            for j in batch:
                if j["url"] not in seen_urls:
                    seen_urls.add(j["url"])
                    all_jobs.append(j)

    if not all_jobs:
        log.info("staff.am: no jobs parsed (site layout may have changed).")
        return {"new": 0, "existing": 0, "total": 0}

    conn = get_connection()
    new, existing = store_jobs(conn, all_jobs, SITE_LABEL, "staff_am_http")
    log.info("staff.am: stored %d new, %d duplicates (parsed %d)", new, existing, len(all_jobs))
    return {"new": new, "existing": existing, "total": len(all_jobs)}
