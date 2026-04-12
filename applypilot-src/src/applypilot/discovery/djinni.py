"""Djinni discovery — scrape jobs from djinni.co."""

import logging
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup

from applypilot import config
from applypilot.database import get_connection, init_db, store_jobs

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
SITE_LABEL = "Djinni"
LIST_BASE = "https://djinni.co/jobs/"


def _parse_listing_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    seen = set()

    for item in soup.select('.job-list-item'):
        a = item.select_one('.job-list-item__link')
        if not a:
            continue
            
        href = a.get("href") or ""
        url = urljoin("https://djinni.co", href.split("?")[0])
        if url in seen: continue
        seen.add(url)
        
        title = (a.get_text() or "").strip()
        
        company = ""
        company_tag = item.select_one('details, .job-list-item__company, .job-list-item__company-name')
        if company_tag:
            company = company_tag.get_text(" ", strip=True).strip()

        loc = "Remote"
        
        # Djinni often has description previews inside the item
        desc_preview = ""
        desc_div = item.select_one('.job-list-item__description, .profile')
        if desc_div:
            desc_preview = desc_div.get_text("\n", strip=True)

        jobs.append({
            "url": url,
            "title": title,
            "company": company,
            "salary": None,
            "description": desc_preview,
            "location": loc,
        })
    return jobs


def fetch_djinni_page(query: str | None = None, page: int = 1) -> list[dict]:
    params: dict[str, str] = {}
    if query:
        params["all-keywords"] = query
    if page > 1:
        params["page"] = str(page)

    try:
        with httpx.Client(timeout=45.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
            r = client.get(LIST_BASE, params=params or None)
            r.raise_for_status()
    except Exception as e:
        log.error("Djinni fetch failed: %s", e)
        return []

    return _parse_listing_html(r.text)


def run_djinni_discovery(max_pages_per_keyword: int = 2) -> dict:
    init_db()
    cfg = config.load_search_config() or {}
    
    keywords = []
    for q in cfg.get("queries", []):
        if isinstance(q, dict) and q.get("query"):
            keywords.append(str(q["query"]))

    if not keywords:
        keywords = [""]

    seen = set()
    all_jobs: list[dict] = []

    for kw in keywords:
        for page in range(1, max_pages_per_keyword + 1):
            batch = fetch_djinni_page(query=kw, page=page)
            if not batch: 
                break
            for j in batch:
                if j["url"] not in seen:
                    seen.add(j["url"])
                    all_jobs.append(j)

    if not all_jobs:
        return {"new": 0, "existing": 0, "total": 0}

    conn = get_connection()
    new, existing = store_jobs(conn, all_jobs, SITE_LABEL, "djinni_http")
    log.info("Djinni: stored %d new, %d duplicates", new, existing)
    return {"new": new, "existing": existing, "total": len(all_jobs)}
