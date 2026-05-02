"""Standalone job discovery: staff.am, LinkedIn/Indeed (python-jobspy), Telegram channels.

All scrapers store results directly in Supabase via users_db.
No ApplyPilot dependency — uses httpx + BeautifulSoup for HTML scraping,
python-jobspy for LinkedIn/Indeed.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, unquote, parse_qs, urlencode, urlunparse

import httpx
from bs4 import BeautifulSoup

from armapply.users_db import upsert_jobs_batch, get_user_preferences

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)

def clean_job_url(url: str) -> str:
    """Remove common tracking parameters from a job URL."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        qs = parse_qs(parsed.query, keep_blank_values=True)
        strip_params = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "refId", "trackingId", "trk", "position", "pageNum", "f_C", "f_TPR",
            "currentJobId", "eBP", "sid", "original_referer"
        }
        filtered_qs = {k: v for k, v in qs.items() if k not in strip_params}
        new_query = urlencode(filtered_qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url



# ═══════════════════════════════════════════════════════════════════════════
# Staff.am scraper
# ═══════════════════════════════════════════════════════════════════════════

STAFFAM_LIST_BASE = "https://staff.am/en/jobs"


def _staffam_normalize_url(href: str) -> str | None:
    if not href or href.startswith("#"):
        return None
    full = urljoin(STAFFAM_LIST_BASE, href)
    parsed = urlparse(full)
    if "staff.am" not in parsed.netloc:
        return None
    path = parsed.path or ""
    if "/company/" in path:
        return None
    if not re.search(r"/(?:en|hy)/jobs/[^/]+/.+", path):
        return None
    return f"{parsed.scheme}://{parsed.netloc}{path.split('?')[0]}"


def _staffam_parse_listing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    seen: set[str] = set()

    for a in soup.select('a[href*="/jobs/"]'):
        href = a.get("href") or ""
        url = _staffam_normalize_url(href)
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
            for city in ("Yerevan", "Gyumri", "Vanadzor", "Remote", "Հեռdelays"):
                if city.lower() in text.lower():
                    loc = city if city != "Հեռdays" else "Remote"
                    break

        jobs.append({
            "url": url,
            "title": title,
            "company": company,
            "salary": None,
            "description": None,
            "location": loc or "Armenia",
        })

    return jobs


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

    desc_box = soup.select_one(".job-list-detail") or soup.select_one(".job-description")
    if not desc_box:
        desc_box = soup.select_one(".job-details-info")

    full_text = ""
    if desc_box:
        full_text = desc_box.get_text("\n", strip=True)

    apply_url = None
    apply_btn = soup.select_one('a[href*="/add-cv-submits"]')
    if apply_btn:
        href = apply_btn.get("href", "")
        if "originUrl=" in href:
            match = re.search(r"originUrl=([^&]+)", href)
            if match:
                apply_url = unquote(match.group(1))
        if not apply_url:
            apply_url = urljoin(STAFFAM_LIST_BASE, href)

    # Email detection
    email = None
    if full_text:
        match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', full_text)
        email = match.group(0) if match else None

    return {
        "full_description": full_text,
        "application_url": apply_url,
        "application_email": email,
    }


def scrape_staffam(
    keywords: list[str] | None = None,
    max_pages_per_keyword: int = 2,
) -> list[dict]:
    """Discover jobs on staff.am. Returns list of job dicts."""
    if not keywords:
        keywords = [""]

    seen_urls: set[str] = set()
    all_jobs: list[dict] = []

    for kw in keywords:
        for page in range(1, max_pages_per_keyword + 1):
            params: dict[str, str] = {}
            if kw:
                params["search"] = kw
            if page > 1:
                params["page"] = str(page)

            try:
                with httpx.Client(timeout=45.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
                    r = client.get(STAFFAM_LIST_BASE, params=params or None)
                    r.raise_for_status()
            except Exception as e:
                log.error("staff.am fetch failed: %s", e)
                break

            batch = _staffam_parse_listing(r.text)
            if not batch:
                break
            for j in batch:
                if j["url"] not in seen_urls:
                    seen_urls.add(j["url"])
                    all_jobs.append(j)

    log.info("staff.am: parsed %d jobs", len(all_jobs))
    return all_jobs


# ═══════════════════════════════════════════════════════════════════════════
# LinkedIn / Indeed scraper (via python-jobspy)
# ═══════════════════════════════════════════════════════════════════════════

def _get_valid_country(country: str | None) -> str:
    """Validate and map country string to JobSpy supported list."""
    if not country:
        return "worldwide"
    c = country.lower().strip()
    if c in ("arm", "am", "armenia"):
        return "worldwide"
    return c


def scrape_jobspy(
    queries: list[str],
    locations: list[dict],
    sites: list[str] | None = None,
    results_per_site: int = 60,
    hours_old: int = 168,
    country: str = "worldwide",
) -> list[dict]:
    """Discover jobs via python-jobspy. Returns list of job dicts."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log.error("python-jobspy not installed, skipping LinkedIn/Indeed discovery")
        return []

    if sites is None:
        sites = ["linkedin"]

    country_val = _get_valid_country(country)
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    for query in queries:
        for loc_cfg in locations:
            location = loc_cfg.get("location", "Remote")
            is_remote = loc_cfg.get("remote", False)

            kwargs = {
                "site_name": sites,
                "search_term": query,
                "location": location,
                "results_wanted": results_per_site,
                "hours_old": hours_old,
                "description_format": "markdown",
                "country_indeed": country_val,
                "country": country_val,
                "verbose": 0,
            }
            if is_remote:
                kwargs["is_remote"] = True
            if "linkedin" in sites:
                kwargs["linkedin_fetch_description"] = True

            try:
                df = scrape_jobs(**kwargs)
            except Exception as e:
                err = str(e).lower()
                if "invalid country string" in err:
                    kwargs["country_indeed"] = "worldwide"
                    kwargs["country"] = "worldwide"
                    loc_str = kwargs.get("location", "")
                    if "," in loc_str:
                        kwargs["location"] = loc_str.split(",")[0].strip()
                    try:
                        df = scrape_jobs(**kwargs)
                    except Exception as e2:
                        log.error("JobSpy retry failed for '%s' in %s: %s", query, location, e2)
                        continue
                else:
                    log.error("JobSpy search failed for '%s' in %s: %s", query, location, e)
                    continue

            for _, row in df.iterrows():
                url = clean_job_url(str(row.get("job_url", "")))
                if not url or url == "nan" or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = str(row.get("title", "")) if str(row.get("title", "")) != "nan" else None
                description = str(row.get("description", "")) if str(row.get("description", "")) != "nan" else None
                location_str = str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None
                site_name = str(row.get("site", "linkedin"))
                row_remote = row.get("is_remote", False)

                if row_remote and location_str:
                    location_str = f"{location_str} (Remote)"
                elif row_remote:
                    location_str = "Remote"

                # Parse salary
                salary = None
                min_amt = row.get("min_amount")
                max_amt = row.get("max_amount")
                interval = str(row.get("interval", "")) if str(row.get("interval", "")) != "nan" else ""
                currency = str(row.get("currency", "")) if str(row.get("currency", "")) != "nan" else ""
                if min_amt and str(min_amt) != "nan":
                    if max_amt and str(max_amt) != "nan":
                        salary = f"{currency}{int(float(min_amt)):,}-{currency}{int(float(max_amt)):,}"
                    else:
                        salary = f"{currency}{int(float(min_amt)):,}"
                    if interval:
                        salary += f"/{interval}"

                full_description = None
                if description and len(description) > 200:
                    full_description = description

                apply_url = str(row.get("job_url_direct", "")) if str(row.get("job_url_direct", "")) != "nan" else None

                all_jobs.append({
                    "url": url,
                    "title": title,
                    "salary": salary,
                    "description": description,
                    "full_description": full_description,
                    "location": location_str,
                    "site": site_name,
                    "application_url": apply_url,
                })

    log.info("JobSpy: parsed %d jobs", len(all_jobs))
    return all_jobs


# ═══════════════════════════════════════════════════════════════════════════
# Telegram channel scraper
# ═══════════════════════════════════════════════════════════════════════════

def _tg_normalize_username(raw: str) -> str:
    s = (raw or "").strip().lstrip("@").strip()
    s = re.sub(r"^https?://t\.me/(s/)?", "", s, flags=re.I)
    s = s.split("/")[0].split("?")[0]
    return s


def _tg_matches_keywords(text: str, keywords: list[str] | None) -> bool:
    if not keywords:
        return True
    low = text.lower()
    return any(k.lower() in low for k in keywords if k)


def _tg_parse_channel_page(html: str, channel_username: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    site_label = f"Telegram:@{channel_username}"

    for msg in soup.select(".tgme_widget_message"):
        date_a = msg.select_one("a.tgme_widget_message_date")
        if not date_a or not date_a.get("href"):
            continue
        href = date_a["href"].strip()
        if not href.startswith("http"):
            href = urljoin("https://t.me/", href)
        parsed = urlparse(href)
        if "t.me" not in parsed.netloc:
            continue

        text_el = msg.select_one(".tgme_widget_message_text")
        raw_text = text_el.get_text("\n", strip=True) if text_el else ""
        if not raw_text or len(raw_text) < 20:
            continue

        title = raw_text.split("\n")[0].strip()[:200] or f"Post {href.rsplit('/', 1)[-1]}"

        out.append({
            "url": href.split("?")[0],
            "title": title,
            "description": raw_text[:2000],
            "full_description": raw_text[:12000],
            "location": "Telegram",
            "site": site_label,
        })

    return out


def _tg_last_before_param(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    ids: list[int] = []
    for msg in soup.select(".tgme_widget_message[data-post]"):
        dp = msg.get("data-post") or ""
        parts = dp.split("/")
        if len(parts) == 2 and parts[1].isdigit():
            ids.append(int(parts[1]))
    return str(min(ids)) if ids else None


def scrape_telegram_channels(
    channels: list[str],
    max_pages_per_channel: int = 2,
    keyword_filter: list[str] | None = None,
) -> list[dict]:
    """Discover jobs from public Telegram channels. Returns list of job dicts."""
    if not channels:
        return []

    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    for ch in channels:
        user = _tg_normalize_username(ch)
        if not user:
            continue

        before: str | None = None
        with httpx.Client(timeout=45.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
            for _ in range(max(1, max_pages_per_channel)):
                url = f"https://t.me/s/{user}"
                params = {}
                if before:
                    params["before"] = before
                try:
                    r = client.get(url, params=params or None)
                    r.raise_for_status()
                except Exception as e:
                    log.error("Telegram channel fetch failed @%s: %s", user, e)
                    break

                batch = _tg_parse_channel_page(r.text, user)
                if not batch:
                    break

                for j in batch:
                    if _tg_matches_keywords(j["full_description"], keyword_filter):
                        if j["url"] not in seen_urls:
                            seen_urls.add(j["url"])
                            all_jobs.append(j)

                nxt = _tg_last_before_param(r.text)
                if not nxt or nxt == before:
                    break
                before = nxt

        log.info("Telegram @%s: %d posts parsed", user, len([j for j in all_jobs if user in j.get("site", "")]))

    log.info("Telegram channels: %d total jobs", len(all_jobs))
    return all_jobs


# ═══════════════════════════════════════════════════════════════════════════
# Enrichment — scrape full descriptions for jobs that lack them
# ═══════════════════════════════════════════════════════════════════════════

def enrich_staffam_jobs(user_id: int, limit: int = 20) -> dict:
    """Fetch full descriptions for staff.am jobs that lack them."""
    from armapply.users_db import _exec, update_job_field

    rows = _exec(
        "SELECT url FROM jobs WHERE user_id = %s AND site = 'staff.am' "
        "AND full_description IS NULL AND detail_error IS NULL LIMIT %s",
        (user_id, limit), fetch="all"
    )
    if not rows:
        return {"enriched": 0}

    enriched = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        url = row["url"]
        details = scrape_staffam_job_details(url)
        if details.get("full_description"):
            update_job_field(user_id, url, "full_description", details["full_description"])
            update_job_field(user_id, url, "detail_scraped_at", now)
            if details.get("application_url"):
                update_job_field(user_id, url, "application_url", details["application_url"])
            if details.get("application_email"):
                update_job_field(user_id, url, "application_email", details["application_email"])
            enriched += 1
        else:
            update_job_field(user_id, url, "detail_error", "no_description_found")

    log.info("Enrichment: %d staff.am jobs enriched for user %d", enriched, user_id)
    return {"enriched": enriched}


# ═══════════════════════════════════════════════════════════════════════════
# Full discovery orchestrator
# ═══════════════════════════════════════════════════════════════════════════

def run_full_discovery(user_id: int, search_config: dict | None = None) -> dict:
    """Run all discovery sources for a user and store results in Supabase.

    Args:
        user_id: The user to discover for.
        search_config: Optional search config dict. If None, loads from user preferences.

    Returns:
        Dict with stats: staffam, jobspy, telegram, total_new, total_updated.
    """
    from armapply.telegram_notify import send_telegram_message

    if search_config is None:
        prefs = get_user_preferences(user_id)
        search_config = prefs.get("search_config", {})

    # Extract settings
    queries_en = [
        q["query"] if isinstance(q, dict) else q
        for q in search_config.get("queries", [{"query": "software developer"}])
    ]
    locations = search_config.get("locations", [
        {"location": "Remote", "remote": True},
        {"location": "Yerevan, Armenia", "remote": False},
    ])
    sites = search_config.get("sites", ["linkedin"])
    staff_am_cfg = search_config.get("staff_am", {"enabled": True})
    tg_channels_cfg = search_config.get("telegram_channels", {})

    results = {"staffam": {}, "jobspy": {}, "telegram": {}, "enrichment": {}}
    total_new = 0
    total_updated = 0

    # 1. Staff.am
    if staff_am_cfg.get("enabled", True):
        try:
            keywords = queries_en + (staff_am_cfg.get("extra_keywords") or [])
            max_pages = int(staff_am_cfg.get("max_pages_per_keyword", 2))
            staffam_jobs = scrape_staffam(keywords=keywords, max_pages_per_keyword=max_pages)
            if staffam_jobs:
                new_c, up_c = upsert_jobs_batch(user_id, staffam_jobs, "staff.am", "staff_am_http")
                results["staffam"] = {"new": new_c, "updated": up_c, "parsed": len(staffam_jobs)}
                total_new += new_c
                total_updated += up_c
        except Exception as e:
            log.error("staff.am discovery failed: %s", e)
            results["staffam"] = {"error": str(e)}

    # 2. LinkedIn / Indeed (JobSpy)
    try:
        jobspy_jobs = scrape_jobspy(
            queries=queries_en,
            locations=locations,
            sites=sites,
            results_per_site=search_config.get("defaults", {}).get("results_per_site", 60),
            hours_old=search_config.get("defaults", {}).get("hours_old", 168),
            country=search_config.get("country", "worldwide"),
        )
        if jobspy_jobs:
            new_c, up_c = upsert_jobs_batch(user_id, jobspy_jobs, "jobspy", "jobspy")
            results["jobspy"] = {"new": new_c, "updated": up_c, "parsed": len(jobspy_jobs)}
            total_new += new_c
            total_updated += up_c
    except Exception as e:
        log.error("JobSpy discovery failed: %s", e)
        results["jobspy"] = {"error": str(e)}

    # 3. Telegram channels
    if tg_channels_cfg.get("enabled", False):
        try:
            channels = tg_channels_cfg.get("channels", [])
            max_pages = int(tg_channels_cfg.get("max_pages_per_channel", 2))
            kw_filter = tg_channels_cfg.get("keyword_filter")
            tg_jobs = scrape_telegram_channels(
                channels=channels,
                max_pages_per_channel=max_pages,
                keyword_filter=kw_filter,
            )
            if tg_jobs:
                new_c, up_c = upsert_jobs_batch(user_id, tg_jobs, "telegram", "telegram_channel")
                results["telegram"] = {"new": new_c, "updated": up_c, "parsed": len(tg_jobs)}
                total_new += new_c
                total_updated += up_c
        except Exception as e:
            log.error("Telegram discovery failed: %s", e)
            results["telegram"] = {"error": str(e)}

    # 4. Enrichment (staff.am details)
    try:
        enrich_result = enrich_staffam_jobs(user_id, limit=20)
        results["enrichment"] = enrich_result
    except Exception as e:
        log.error("Enrichment failed: %s", e)
        results["enrichment"] = {"error": str(e)}

    results["total_new"] = total_new
    results["total_updated"] = total_updated

    # Notify user about new jobs
    if total_new > 0:
        prefs = get_user_preferences(user_id)
        chat_id = prefs.get("telegram_chat_id")
        bot_token = prefs.get("telegram_bot_token")
        if chat_id and bot_token:
            send_telegram_message(str(chat_id), f"🔍 ArmApply: Found {total_new} new jobs for you!", bot_token=bot_token)

    log.info("Full discovery for user %d: %d new, %d updated", user_id, total_new, total_updated)
    return results
