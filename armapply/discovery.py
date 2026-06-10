"""Job discovery — staff.am, LinkedIn (via python-jobspy), Telegram channels.

Each source produces `RawJob` dicts; the orchestrator dedupes (via `db.url_hash`)
and persists with `db.upsert_job`. The worldwide ratio is enforced *after*
staff.am has run, so LinkedIn never dominates the Armenia-first mix.
"""

from __future__ import annotations

import logging
import math
import re
from typing import TYPE_CHECKING, Iterable, TypedDict
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# job.am serves RSS that we parse with html.parser to avoid an lxml dep.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from armapply import db

if TYPE_CHECKING:
    from armapply.db import JobSource, User

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "refid", "trackingid", "trk", "position", "pagenum",
    "currentjobid", "ebp", "sid", "original_referer",
})


class RawJob(TypedDict):
    url: str
    source: "JobSource"
    title: str
    company: str | None
    location: str | None
    description: str | None
    salary: str | None
    recruiter_email: str | None


# ---------------------------------------------------------------------------
# URL hygiene
# ---------------------------------------------------------------------------

def clean_url(url: str) -> str:
    """Strip tracking params and fragments. Lower-cases the host."""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return url.strip()
    if not p.netloc:
        return url.strip()
    qs = parse_qs(p.query, keep_blank_values=True)
    qs = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
    return urlunparse(p._replace(
        netloc=p.netloc.lower(),
        query=urlencode(qs, doseq=True),
        fragment="",
    ))


def extract_email(text: str | None) -> str | None:
    if not text:
        return None
    m = EMAIL_RE.search(text)
    return m.group(0).lower() if m else None


# ---------------------------------------------------------------------------
# staff.am
# ---------------------------------------------------------------------------

STAFFAM_BASE = "https://staff.am/en/jobs"
STAFFAM_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# Title fragments for obviously non-tech jobs. Used to drop listings before
# we spend LLM budget on them. Case-insensitive substring match.
_NON_TECH_TITLE_HINTS = (
    "accountant", "tax ", "auditor", "bookkeep", "cashier", "teller",
    "lawyer", "attorney", "legal counsel", "notary",
    "barista", "waiter", "waitress", "cook ", "chef", "cleaner", "janitor",
    "driver", "courier", "warehouse", "guard", "security officer",
    "sales manager", "sales agent", "sales representative", "salesperson",
    "retail ", "store ", "shop assistant", "merchandiser",
    "smm ", "marketing assistant", "brand manager", "copywriter",
    "hr ", "recruiter", "human resources",
    "doctor", "nurse", "pharmacist", "dentist",
    "teacher", "tutor", "trainer ",
    "operator", "consultant ",  # bank operator, financial consultant, etc.
    "loan ", "credit specialist", "underwriter",
    "գանձապահ", "վաճառող", "օպերատոր",  # cashier, salesperson, operator in Armenian
)


def _looks_non_tech(title: str) -> bool:
    """Return True for jobs whose titles obviously don't match a tech role.
    Used to skip enrichment + scoring without an LLM call."""
    if not title:
        return False
    low = title.lower()
    return any(hint in low for hint in _NON_TECH_TITLE_HINTS)


def _staffam_url_ok(href: str) -> str | None:
    if not href or href.startswith("#"):
        return None
    full = urljoin(STAFFAM_BASE, href)
    p = urlparse(full)
    if "staff.am" not in p.netloc:
        return None
    path = p.path or ""
    if "/company/" in path:
        return None
    if not re.search(r"/(?:en|hy)/jobs/[^/]+/.+", path):
        return None
    return clean_url(f"{p.scheme}://{p.netloc}{path}")


def _staffam_parse_card(card) -> tuple[str | None, str | None]:
    """Return (company, location) for a staff.am job card."""
    company: str | None = None
    for link in card.select('a[href*="/company/"]'):
        company = (link.get_text() or "").strip() or None
        if company:
            break

    location: str | None = None
    text = card.get_text(" ", strip=True).lower()
    for city in ("yerevan", "gyumri", "vanadzor", "remote"):
        if city in text:
            location = city.title() if city != "remote" else "Remote"
            break
    return company, location or "Armenia"


def _staffam_list_page(client: httpx.Client, page: int) -> list[RawJob]:
    # NOTE: staff.am's `?search=` param is a no-op for guest requests — the
    # category UI is JS-only. We just fetch the firehose and filter by title
    # keywords later. Cheaper and more accurate than guessing search terms.
    params = {"page": str(page)} if page > 1 else None
    r = client.get(STAFFAM_BASE, params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    seen: set[str] = set()
    out: list[RawJob] = []
    for a in soup.select('a[href*="/jobs/"]'):
        url = _staffam_url_ok(a.get("href") or "")
        if not url or url in seen:
            continue
        title = (a.get_text() or "").strip()
        if len(title) < 3:
            continue
        seen.add(url)
        card = a.find_parent(["article", "div", "li"])
        company, location = _staffam_parse_card(card) if card else (None, "Armenia")
        out.append(RawJob(
            url=url,
            source="staff_am",
            title=title,
            company=company,
            location=location,
            description=None,
            salary=None,
            recruiter_email=None,
        ))
    return out


def _staffam_enrich(client: httpx.Client, job: RawJob) -> RawJob:
    """Fetch the detail page so we have a description + recruiter email."""
    try:
        r = client.get(job["url"])
        r.raise_for_status()
    except Exception as e:
        log.warning("staff.am detail fetch failed for %s: %s", job["url"], e)
        return job
    soup = BeautifulSoup(r.text, "html.parser")
    box = (
        soup.select_one(".job-list-detail")
        or soup.select_one(".job-description")
        or soup.select_one(".job-details-info")
    )
    description = box.get_text("\n", strip=True) if box else None
    return {**job, "description": description, "recruiter_email": extract_email(description)}


def discover_staffam(_queries: Iterable[str], *, max_pages: int = 5, enrich_top: int = 30) -> list[RawJob]:
    """Scrape the staff.am job firehose and drop obvious non-tech listings.

    `_queries` is unused — staff.am's `?search=` is a no-op for guests, so
    we walk the main feed and filter by title heuristics. Kept in the
    signature so callers (and the orchestrator) don't need to change.
    """
    listings: dict[str, RawJob] = {}  # url -> job
    headers = {"User-Agent": USER_AGENT}
    skipped_non_tech = 0
    with httpx.Client(timeout=STAFFAM_TIMEOUT, headers=headers, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            try:
                page_jobs = _staffam_list_page(client, page)
            except httpx.HTTPError as e:
                log.warning("staff.am page %d failed: %s", page, e)
                break
            if not page_jobs:
                break
            for j in page_jobs:
                if _looks_non_tech(j["title"]):
                    skipped_non_tech += 1
                    continue
                listings.setdefault(j["url"], j)

        to_enrich = list(listings.values())[:enrich_top]
        for j in to_enrich:
            listings[j["url"]] = _staffam_enrich(client, j)
    log.info(
        "staff.am: %d candidate listings (%d enriched, %d non-tech skipped)",
        len(listings), len(to_enrich), skipped_non_tech,
    )
    return list(listings.values())


# ---------------------------------------------------------------------------
# job.am (RSS feed — much cleaner than scraping their JS-rendered HTML)
# ---------------------------------------------------------------------------

JOBAM_FEED = "https://job.am/api/jobs/feed/"


def _jobam_strip_html(html: str | None) -> str | None:
    if not html:
        return None
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


def discover_jobam(*, limit: int = 60) -> list[RawJob]:
    """Parse job.am's public RSS feed. Each item is one vacancy.

    The feed is the cheapest source we have: no JS rendering, no rate limit
    in practice, and bilingual titles. We drop obvious non-tech listings
    via the same heuristic as staff.am.
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        with httpx.Client(timeout=STAFFAM_TIMEOUT, headers=headers, follow_redirects=True) as client:
            r = client.get(JOBAM_FEED)
            r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("job.am feed fetch failed: %s", e)
        return []

    # html.parser handles RSS items fine and avoids requiring lxml.
    soup = BeautifulSoup(r.text, "html.parser")
    out: list[RawJob] = []
    skipped_non_tech = 0
    for item in soup.find_all("item")[:limit * 2]:
        title_el = item.find("title")
        link_el = item.find("link")
        if not title_el or not link_el:
            continue
        title = (title_el.get_text() or "").strip()
        # In some BS4 parsers `<link>` is treated as an empty tag whose URL
        # ends up in the tag *tail* rather than .text. Fall back to next_sibling.
        url_text = (link_el.get_text() or "").strip()
        if not url_text:
            nxt = link_el.next_sibling
            url_text = (str(nxt).strip() if nxt else "")
        if not title or not url_text:
            continue
        if _looks_non_tech(title):
            skipped_non_tech += 1
            continue
        author_el = item.find("author")
        company = (author_el.get_text() or "").strip() if author_el else None
        desc = _jobam_strip_html(item.find("description").get_text() if item.find("description") else None)
        out.append(RawJob(
            url=clean_url(url_text),
            source="job_am",
            title=title,
            company=company,
            location="Armenia",
            description=desc[:8000] if desc else None,
            salary=None,
            recruiter_email=extract_email(desc),
        ))
        if len(out) >= limit:
            break
    log.info("job.am: %d listings (%d non-tech skipped)", len(out), skipped_non_tech)
    return out


# ---------------------------------------------------------------------------
# myjob.am (classic HTML, .shortJobContainer cards)
# ---------------------------------------------------------------------------

MYJOB_BASE = "https://www.myjob.am/"


def _myjob_parse_card(a) -> RawJob | None:
    href = a.get("href") or ""
    if not href or "Announcement.aspx" not in href:
        return None
    url = clean_url(urljoin(MYJOB_BASE, href))
    title_el = a.select_one(".shortJobPosition")
    company_el = a.select_one(".shortJobCompany")
    addr_el = a.select_one(".shortJobAddress")
    if not title_el:
        return None
    title = (title_el.get_text() or "").strip()
    if not title:
        return None
    return RawJob(
        url=url,
        source="myjob_am",
        title=title,
        company=(company_el.get_text() or "").strip() if company_el else None,
        location=(addr_el.get_text() or "").strip() if addr_el else "Armenia",
        description=None,
        salary=None,
        recruiter_email=None,
    )


def _myjob_enrich(client: httpx.Client, job: RawJob) -> RawJob:
    try:
        r = client.get(job["url"])
        r.raise_for_status()
    except Exception as e:
        log.warning("myjob.am detail fetch failed for %s: %s", job["url"], e)
        return job
    soup = BeautifulSoup(r.text, "html.parser")
    # The detail page uses ASP.NET ids; the main job body is consistently
    # inside one of these containers. We fall back to the whole document
    # text if neither is found, then clip.
    box = (
        soup.select_one("#MainContentPlaceHolder_jobContainer")
        or soup.select_one(".jobContainer")
        or soup.select_one("#MainContentPlaceHolder_jobPageContainer")
    )
    description = box.get_text("\n", strip=True) if box else None
    return {**job, "description": description, "recruiter_email": extract_email(description)}


def discover_myjobam(*, enrich_top: int = 25) -> list[RawJob]:
    headers = {"User-Agent": USER_AGENT}
    out: dict[str, RawJob] = {}
    skipped_non_tech = 0
    with httpx.Client(timeout=STAFFAM_TIMEOUT, headers=headers, follow_redirects=True) as client:
        try:
            r = client.get(MYJOB_BASE)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("myjob.am fetch failed: %s", e)
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select('a[href*="Announcement.aspx"]'):
            j = _myjob_parse_card(a)
            if not j:
                continue
            if _looks_non_tech(j["title"]):
                skipped_non_tech += 1
                continue
            out.setdefault(j["url"], j)

        to_enrich = list(out.values())[:enrich_top]
        for j in to_enrich:
            out[j["url"]] = _myjob_enrich(client, j)
    log.info(
        "myjob.am: %d listings (%d enriched, %d non-tech skipped)",
        len(out), len(to_enrich), skipped_non_tech,
    )
    return list(out.values())


# ---------------------------------------------------------------------------
# LinkedIn (via python-jobspy)
# ---------------------------------------------------------------------------

def _format_salary(min_amt, max_amt, interval, currency) -> str | None:
    def f(x) -> int | None:
        try:
            v = float(x)
            return None if math.isnan(v) else int(v)
        except (TypeError, ValueError):
            return None
    lo, hi = f(min_amt), f(max_amt)
    if lo is None and hi is None:
        return None
    cur = (str(currency) if currency else "").strip()
    cur = "" if cur == "nan" else cur
    if lo is not None and hi is not None:
        out = f"{cur}{lo:,}-{cur}{hi:,}"
    else:
        out = f"{cur}{(lo or hi):,}"
    iv = (str(interval) if interval else "").strip()
    if iv and iv != "nan":
        out += f"/{iv}"
    return out


def _coalesce_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def discover_linkedin(
    queries: Iterable[str],
    locations: Iterable[str],
    *,
    limit: int,
    hours_old: int = 168,
) -> list[RawJob]:
    """Pull LinkedIn jobs via python-jobspy. Capped to `limit` results."""
    if limit <= 0:
        return []
    try:
        from jobspy import scrape_jobs  # type: ignore[import-not-found]
    except ImportError:
        log.warning("python-jobspy not installed; skipping LinkedIn discovery")
        return []

    queries = list(queries)
    locations = list(locations) or ["Remote"]
    if not queries:
        return []

    # Distribute the budget across (query, location) cells.
    per_cell = max(1, limit // max(1, len(queries) * len(locations)))

    out: dict[str, RawJob] = {}
    for q in queries:
        for loc in locations:
            try:
                df = scrape_jobs(
                    site_name=["linkedin"],
                    search_term=q,
                    location=loc,
                    results_wanted=per_cell,
                    hours_old=hours_old,
                    description_format="markdown",
                    country_indeed="worldwide",
                    linkedin_fetch_description=True,
                    verbose=0,
                )
            except Exception as e:
                msg = str(e).lower()
                # jobspy rejects locations it doesn't recognise as supported
                # countries (e.g. "Armenia"). That's expected, not a bug — log
                # quietly and move on.
                if "invalid country string" in msg:
                    log.debug("linkedin: skipping unsupported loc=%r", loc)
                else:
                    log.warning("linkedin: query=%r loc=%r failed: %s", q, loc, e)
                continue

            for _, row in df.iterrows():
                url = clean_url(_coalesce_str(row.get("job_url")) or "")
                if not url or url in out:
                    continue
                description = _coalesce_str(row.get("description"))
                location_str = _coalesce_str(row.get("location"))
                if row.get("is_remote") and location_str:
                    location_str = f"{location_str} (Remote)"
                elif row.get("is_remote"):
                    location_str = "Remote"
                out[url] = RawJob(
                    url=url,
                    source="linkedin",
                    title=_coalesce_str(row.get("title")) or "(untitled)",
                    company=_coalesce_str(row.get("company")),
                    location=location_str,
                    description=description,
                    salary=_format_salary(
                        row.get("min_amount"), row.get("max_amount"),
                        row.get("interval"), row.get("currency"),
                    ),
                    recruiter_email=extract_email(description),
                )
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break

    log.info("linkedin: %d jobs (cap=%d)", len(out), limit)
    return list(out.values())


# ---------------------------------------------------------------------------
# Telegram channels
# ---------------------------------------------------------------------------

def _tg_username(raw: str) -> str:
    s = (raw or "").strip().lstrip("@")
    s = re.sub(r"^https?://t\.me/(s/)?", "", s, flags=re.I)
    return s.split("/")[0].split("?")[0]


def _tg_keyword_match(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    low = text.lower()
    return any(k.lower() in low for k in keywords if k)


def _tg_parse_page(html: str) -> list[RawJob]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[RawJob] = []
    for msg in soup.select(".tgme_widget_message"):
        date_a = msg.select_one("a.tgme_widget_message_date")
        if not date_a or not date_a.get("href"):
            continue
        href = date_a["href"].strip()
        if not href.startswith("http"):
            href = urljoin("https://t.me/", href)
        p = urlparse(href)
        if "t.me" not in p.netloc:
            continue
        text_el = msg.select_one(".tgme_widget_message_text")
        raw = text_el.get_text("\n", strip=True) if text_el else ""
        if len(raw) < 20:
            continue
        title = raw.split("\n", 1)[0].strip()[:200] or f"Post {href.rsplit('/', 1)[-1]}"
        out.append(RawJob(
            url=clean_url(href),
            source="telegram",
            title=title,
            company=None,
            location="Telegram",
            description=raw[:8000],
            salary=None,
            recruiter_email=extract_email(raw),
        ))
    return out


def discover_telegram(
    channels: Iterable[str],
    *,
    keywords: list[str] | None = None,
    max_pages: int = 2,
) -> list[RawJob]:
    out: dict[str, RawJob] = {}
    headers = {"User-Agent": USER_AGENT}
    keywords = keywords or []
    with httpx.Client(timeout=STAFFAM_TIMEOUT, headers=headers, follow_redirects=True) as client:
        for ch in channels:
            user = _tg_username(ch)
            if not user:
                continue
            before: str | None = None
            for _ in range(max(1, max_pages)):
                try:
                    r = client.get(
                        f"https://t.me/s/{user}",
                        params={"before": before} if before else None,
                    )
                    r.raise_for_status()
                except httpx.HTTPError as e:
                    log.warning("telegram @%s fetch failed: %s", user, e)
                    break
                page = _tg_parse_page(r.text)
                if not page:
                    break
                for j in page:
                    if j["url"] in out:
                        continue
                    if not _tg_keyword_match(j["description"] or "", keywords):
                        continue
                    out[j["url"]] = j
                # Find the lowest message id to paginate further back.
                ids = [
                    int(m.get("data-post", "0/0").split("/")[-1])
                    for m in BeautifulSoup(r.text, "html.parser").select(".tgme_widget_message[data-post]")
                    if m.get("data-post", "0/0").split("/")[-1].isdigit()
                ]
                if not ids:
                    break
                nxt = str(min(ids))
                if nxt == before:
                    break
                before = nxt
    log.info("telegram: %d posts across %d channels", len(out), len(list(channels)))
    return list(out.values())


# ---------------------------------------------------------------------------
# Per-user orchestrator
# ---------------------------------------------------------------------------

def _persist(user_id: int, jobs: Iterable[RawJob]) -> tuple[int, int]:
    """Returns (new, updated)."""
    new = updated = 0
    for j in jobs:
        _, inserted = db.upsert_job(
            user_id,
            url=j["url"],
            source=j["source"],
            title=j["title"],
            company=j["company"],
            location=j["location"],
            description=j["description"],
            salary=j["salary"],
            recruiter_email=j["recruiter_email"],
        )
        if inserted:
            new += 1
        else:
            updated += 1
    return new, updated


def discover_for_user(user: "User") -> dict[str, dict[str, int]]:
    """Run all sources for one user. Returns per-source counts."""
    result: dict[str, dict[str, int]] = {}

    # 1) staff.am — Armenia-first, always run.
    staffam = discover_staffam(user["queries"])
    n, u = _persist(user["id"], staffam)
    result["staff_am"] = {"discovered": len(staffam), "new": n, "updated": u}

    # 1b) job.am — bilingual RSS feed, cheap.
    try:
        jobam = discover_jobam()
    except Exception as e:
        log.warning("job.am discovery failed: %s", e)
        jobam = []
    n, u = _persist(user["id"], jobam)
    result["job_am"] = {"discovered": len(jobam), "new": n, "updated": u}

    # 1c) myjob.am — classic HTML board, mostly Yerevan IT.
    try:
        myjob = discover_myjobam()
    except Exception as e:
        log.warning("myjob.am discovery failed: %s", e)
        myjob = []
    n, u = _persist(user["id"], myjob)
    result["myjob_am"] = {"discovered": len(myjob), "new": n, "updated": u}

    # 2) LinkedIn — capped to floor(local_pool * worldwide_ratio), bounded [0, 20].
    local_pool = len(staffam) + len(jobam) + len(myjob)
    cap = max(0, min(20, math.floor(local_pool * user["worldwide_ratio"])))
    linkedin = discover_linkedin(
        user["queries"],
        user["locations"] or ["Remote"],
        limit=cap,
    )
    n, u = _persist(user["id"], linkedin)
    result["linkedin"] = {"discovered": len(linkedin), "new": n, "updated": u, "cap": cap}

    # 3) Telegram channels — only if user subscribed to any.
    if user["telegram_channels"]:
        tg = discover_telegram(
            user["telegram_channels"],
            keywords=user["queries"],
        )
        n, u = _persist(user["id"], tg)
        result["telegram"] = {"discovered": len(tg), "new": n, "updated": u}

    total_new = sum(r.get("new", 0) for r in result.values())
    log.info("user %d: discovery complete, %d new jobs", user["id"], total_new)
    return result
