"""Парсинг публичных Telegram-каналов через веб-ленту t.me/s/<username>.

Работает только для каналов с открытой публичной страницей (без входа в аккаунт).
Каждый пост сохраняется как «вакансия» с URL вида https://t.me/username/123 —
удобно для ручного отклика и для этапа score/tailor (текст поста = описание).

Ограничения: вёрстка t.me может измениться; приватные каналы не поддерживаются.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from applypilot import config
from applypilot.database import get_connection, init_db

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
STRATEGY = "telegram_channel"


def _normalize_username(raw: str) -> str:
    s = (raw or "").strip().lstrip("@").strip()
    s = re.sub(r"^https?://t\.me/(s/)?", "", s, flags=re.I)
    s = s.split("/")[0].split("?")[0]
    return s


def _matches_keywords(text: str, keywords: list[str] | None) -> bool:
    if not keywords:
        return True
    low = text.lower()
    return any(k.lower() in low for k in keywords if k)


def _parse_channel_page(html: str, channel_username: str) -> list[dict]:
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

        out.append(
            {
                "url": href.split("?")[0],
                "title": title,
                "description": raw_text[:2000],
                "full_description": raw_text[:12000],
                "location": "Telegram",
                "site": site_label,
            }
        )

    return out


def _soup_last_before_param(html: str) -> str | None:
    """Минимальный data-post id на странице — для ?before= (старые посты)."""
    soup = BeautifulSoup(html, "html.parser")
    ids: list[int] = []
    for msg in soup.select(".tgme_widget_message[data-post]"):
        dp = msg.get("data-post") or ""
        parts = dp.split("/")
        if len(parts) == 2 and parts[1].isdigit():
            ids.append(int(parts[1]))
    return str(min(ids)) if ids else None


def _store_telegram_posts(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    now = datetime.now(timezone.utc).isoformat()
    new, dup = 0, 0
    for j in jobs:
        try:
            conn.execute(
                "INSERT INTO jobs (url, title, salary, description, location, site, strategy, discovered_at, "
                "full_description, application_url, detail_scraped_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    j["url"],
                    j["title"],
                    None,
                    j["description"],
                    j.get("location", "Telegram"),
                    j["site"],
                    STRATEGY,
                    now,
                    j["full_description"],
                    j["url"],
                    now,
                ),
            )
            new += 1
        except sqlite3.IntegrityError:
            dup += 1
    conn.commit()
    return new, dup


def fetch_channel_messages(
    channel_username: str,
    max_pages: int = 1,
    keyword_filter: list[str] | None = None,
) -> list[dict]:
    """Загрузить до max_pages страниц ленты канала (новые посты первыми)."""
    user = _normalize_username(channel_username)
    if not user:
        return []

    collected: list[dict] = []
    before: str | None = None

    with httpx.Client(timeout=45.0, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for _ in range(max(1, max_pages)):
            url = f"https://t.me/s/{user}"
            params = {}
            if before:
                params["before"] = before
            try:
                r = client.get(url, params=params or None)
                r.raise_for_status()
            except Exception as e:
                log.error("Telegram channel fetch failed %s: %s", user, e)
                break

            batch = _parse_channel_page(r.text, user)
            if not batch:
                break

            for j in batch:
                if _matches_keywords(j["full_description"], keyword_filter):
                    collected.append(j)

            nxt = _soup_last_before_param(r.text)
            if not nxt or nxt == before:
                break
            before = nxt

    seen: set[str] = set()
    unique: list[dict] = []
    for j in collected:
        if j["url"] not in seen:
            seen.add(j["url"])
            unique.append(j)
    return unique


def run_telegram_channel_discovery(
    channels: list[str] | None = None,
    max_pages_per_channel: int = 1,
    keyword_filter: list[str] | None = None,
) -> dict:
    """Сохранить посты из списка публичных каналов в БД ApplyPilot."""
    init_db()
    cfg = config.load_search_config() or {}
    tg_cfg = cfg.get("telegram_channels") or {}
    if not tg_cfg.get("enabled", True) and channels is None:
        return {"new": 0, "existing": 0, "channels": 0, "posts": 0, "skipped": True}

    if channels is None:
        channels = tg_cfg.get("channels") or []
    if not channels:
        log.info("telegram_channels: no channels configured")
        return {"new": 0, "existing": 0, "channels": 0, "posts": 0}

    cfg_pages = int(tg_cfg.get("max_pages_per_channel", 1))
    if max_pages_per_channel < 1:
        max_pages_per_channel = max(1, cfg_pages)

    kw = keyword_filter
    if kw is None:
        kw = tg_cfg.get("keyword_filter") or tg_cfg.get("keywords") or []

    conn = get_connection()
    total_new = 0
    total_dup = 0
    total_posts = 0

    for ch in channels:
        jobs = fetch_channel_messages(ch, max_pages=max_pages_per_channel, keyword_filter=kw or None)
        total_posts += len(jobs)
        n, d = _store_telegram_posts(conn, jobs)
        total_new += n
        total_dup += d
        log.info("Telegram @%s: %d posts parsed, %d new, %d dupes", _normalize_username(ch), len(jobs), n, d)

    return {
        "new": total_new,
        "existing": total_dup,
        "channels": len(channels),
        "posts_parsed": total_posts,
    }
