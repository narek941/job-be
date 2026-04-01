"""Дневные лимиты откликов, задержки между откликами, правила «умной» очереди."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from armapply.users_db import get_user_preferences


def prefs_smart(prefs: dict) -> dict[str, Any]:
    return prefs.get("smart_apply") or {}


def apply_limits_snapshot(user_id: int, prefs: dict | None = None) -> dict[str, Any]:
    """Сводка лимитов без проверки (для GET /apply/limits-status)."""
    prefs = prefs if prefs is not None else get_user_preferences(user_id)
    smart = prefs_smart(prefs)
    max_day = smart.get("max_applies_per_day")
    if max_day is None:
        max_day = prefs.get("max_applies_per_day")
    min_delay = smart.get("min_delay_seconds_between_applies")
    if min_delay is None:
        min_delay = prefs.get("min_delay_seconds_between_applies")
    used = count_applies_today_utc(user_id)
    last = last_apply_time_utc(user_id)
    return {
        "applies_today_utc": used,
        "max_applies_per_day": max_day,
        "min_delay_seconds_between_applies": min_delay,
        "last_apply_at_utc": last.isoformat() if last else None,
        "smart_apply": smart,
    }


def count_applies_today_utc(user_id: int) -> int:
    """Подсчёт откликов за календарные сутки UTC по таблице jobs пользователя."""
    from armapply.workspace import activate_user_workspace
    from applypilot.database import get_connection

    activate_user_workspace(user_id)
    conn = get_connection()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        """
        SELECT COUNT(*) FROM jobs
        WHERE applied_at IS NOT NULL
          AND (apply_status IS NULL OR apply_status NOT IN ('failed', 'in_progress'))
          AND substr(replace(applied_at, 'T', ' '), 1, 10) = ?
        """,
        (day,),
    ).fetchone()
    return int(row[0]) if row else 0


def last_apply_time_utc(user_id: int) -> datetime | None:
    from armapply.workspace import activate_user_workspace
    from applypilot.database import get_connection

    activate_user_workspace(user_id)
    row = get_connection().execute(
        "SELECT applied_at FROM jobs WHERE applied_at IS NOT NULL ORDER BY applied_at DESC LIMIT 1"
    ).fetchone()
    if not row or not row[0]:
        return None
    raw = str(row[0])
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def assert_apply_allowed(user_id: int, prefs: dict | None = None) -> dict[str, Any]:
    """Бросает HTTPException 429 если лимит или cooldown нарушены. Возвращает статус для ответа API."""
    prefs = prefs if prefs is not None else get_user_preferences(user_id)
    smart = prefs_smart(prefs)

    max_day = smart.get("max_applies_per_day")
    if max_day is None:
        max_day = prefs.get("max_applies_per_day")
    if max_day is not None:
        max_day = int(max_day)
        used = count_applies_today_utc(user_id)
        if used >= max_day:
            raise HTTPException(
                status_code=429,
                detail=f"Дневной лимит откликов достигнут ({used}/{max_day}). Попробуйте завтра (UTC).",
            )

    min_delay = smart.get("min_delay_seconds_between_applies")
    if min_delay is None:
        min_delay = prefs.get("min_delay_seconds_between_applies")
    if min_delay is not None:
        min_delay = int(min_delay)
        last = last_apply_time_utc(user_id)
        if last:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed < min_delay:
                retry = int(min_delay - elapsed) + 1
                raise HTTPException(
                    status_code=429,
                    detail=f"Слишком рано после предыдущего отклика. Подождите ~{retry} с (правило smart_apply).",
                    headers={"Retry-After": str(retry)},
                )

    return {
        "applies_today_utc": count_applies_today_utc(user_id),
        "max_applies_per_day": max_day,
        "min_delay_seconds": min_delay,
    }


def fetch_smart_apply_queue(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """Вакансии с готовым tailored resume, ещё не откликались, с учётом приоритета по score и «отдыха» компании."""
    from armapply.workspace import activate_user_workspace
    from applypilot.database import get_connection

    prefs = get_user_preferences(user_id)
    smart = prefs_smart(prefs)
    activate_user_workspace(user_id)
    conn = get_connection()

    skip_days = int(smart.get("skip_same_site_days", 0) or 0)
    prefer_score = smart.get("prefer_higher_score_first", True)

    order_sql = "fit_score DESC NULLS LAST, discovered_at DESC" if prefer_score else "discovered_at DESC"

    rows = conn.execute(
        f"""
        SELECT * FROM jobs
        WHERE tailored_resume_path IS NOT NULL
          AND (applied_at IS NULL OR apply_status = 'failed')
          AND (application_url IS NOT NULL OR url IS NOT NULL)
        ORDER BY {order_sql}
        LIMIT 200
        """,
    ).fetchall()

    # Недавно откликавшиеся сайты (грубый proxy «компании»)
    recent_sites: set[str] = set()
    if skip_days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=skip_days)).isoformat()
        prev = conn.execute(
            "SELECT DISTINCT site FROM jobs WHERE applied_at IS NOT NULL AND applied_at >= ?",
            (cutoff,),
        ).fetchall()
        recent_sites = {str(p[0] or "") for p in prev}

    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        sk = d.get("site") or ""
        if skip_days > 0 and sk and sk in recent_sites:
            continue
        out.append(d)
        if len(out) >= limit:
            break

    return out
