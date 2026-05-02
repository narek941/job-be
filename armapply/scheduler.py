"""Pipeline runner — stateless functions called by API endpoints.

No scheduler, no background threads. GitHub Actions triggers
the /pipeline/trigger endpoint on a daily cron schedule.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def run_pipeline_for_user(user_id: int) -> dict:
    """Run the full pipeline for a single user: discover → score → cover letters → notify."""
    from armapply.discovery import run_full_discovery
    from armapply.scoring import score_jobs_for_user
    from armapply.users_db import _exec, update_job_field, log_pipeline_run
    from armapply.llm_features import generate_tailored_cover_letter
    from armapply.telegram_notify import send_job_match_notification, send_daily_summary

    result = {"user_id": user_id, "discovery": {}, "scoring": {}, "covers": 0, "notified": 0}

    # 1. Discovery
    try:
        log.info("[User %d] Running discovery...", user_id)
        disc = run_full_discovery(user_id)
        result["discovery"] = disc
        log_pipeline_run(user_id, "discover", "ok", f"new={disc.get('total_new', 0)}")
    except Exception as e:
        log.error("[User %d] Discovery failed: %s", user_id, e)
        log_pipeline_run(user_id, "discover", "error", str(e)[:1000])
        result["discovery"] = {"error": str(e)}

    # 2. Scoring
    try:
        log.info("[User %d] Running scoring...", user_id)
        score_res = score_jobs_for_user(user_id, limit=20)
        result["scoring"] = score_res
        log_pipeline_run(user_id, "score", "ok", f"scored={score_res.get('scored', 0)}")
    except Exception as e:
        log.error("[User %d] Scoring failed: %s", user_id, e)
        log_pipeline_run(user_id, "score", "error", str(e)[:1000])
        result["scoring"] = {"error": str(e)}

    # 3. Cover letters for high-scoring jobs
    try:
        rows = _exec(
            "SELECT url, title, site, location, description, full_description, fit_score "
            "FROM jobs WHERE user_id = %s AND fit_score >= 7 "
            "AND cover_letter_text IS NULL "
            "ORDER BY fit_score DESC LIMIT 5",
            (user_id,), fetch="all"
        )
        now = datetime.now(timezone.utc).isoformat()
        for row in (rows or []):
            try:
                cover = generate_tailored_cover_letter(dict(row), user_id=user_id)
                update_job_field(user_id, row["url"], "cover_letter_text", cover)
                update_job_field(user_id, row["url"], "cover_letter_at", now)
                result["covers"] += 1
            except Exception as e:
                log.error("[User %d] Cover letter failed: %s", user_id, e)
    except Exception as e:
        log.error("[User %d] Cover letter sweep failed: %s", user_id, e)

    # 4. Telegram notifications for high-scoring matches
    from armapply.users_db import get_user_preferences
    prefs = get_user_preferences(user_id)
    chat_id = prefs.get("telegram_chat_id")
    bot_token = prefs.get("telegram_bot_token")  # per-user bot

    if chat_id and bot_token:
        try:
            rows = _exec(
                "SELECT url, title, site, location, fit_score, score_reasoning, "
                "cover_letter_text, salary "
                "FROM jobs WHERE user_id = %s AND fit_score >= 7 "
                "AND (agent_id IS NULL OR agent_id != 'notified') "
                "ORDER BY fit_score DESC LIMIT 5",
                (user_id,), fetch="all"
            )
            for row in (rows or []):
                try:
                    send_job_match_notification(
                        chat_id=str(chat_id),
                        job=dict(row),
                        cover_letter_text=row.get("cover_letter_text"),
                        bot_token=bot_token,
                    )
                    update_job_field(user_id, row["url"], "agent_id", "notified")
                    result["notified"] += 1
                except Exception as e:
                    log.error("[User %d] Notification failed: %s", user_id, e)

            # Daily summary
            stats = _exec(
                "SELECT "
                "COUNT(*) FILTER (WHERE discovered_at::timestamp > NOW() - INTERVAL '24 hours') as new_today, "
                "COUNT(*) FILTER (WHERE fit_score >= 7) as high_scoring, "
                "COUNT(*) as total "
                "FROM jobs WHERE user_id = %s",
                (user_id,), fetch="one"
            )
            if stats and stats.get("new_today", 0) > 0:
                send_daily_summary(str(chat_id), dict(stats), bot_token=bot_token)

        except Exception as e:
            log.error("[User %d] Notification sweep failed: %s", user_id, e)

    log.info("[User %d] Pipeline complete: %s", user_id, result)
    return result


def run_pipeline_all_users() -> dict:
    """Run the full pipeline for ALL users with autopilot enabled."""
    from armapply.users_db import get_users_with_autopilot

    users = get_users_with_autopilot()
    if not users:
        log.info("No active users with autopilot enabled.")
        return {"users_processed": 0, "results": []}

    results = []
    for user in users:
        uid = user["id"]
        try:
            res = run_pipeline_for_user(uid)
            results.append(res)
        except Exception as e:
            log.error("[User %d] Full pipeline failed: %s", uid, e)
            results.append({"user_id": uid, "error": str(e)})

    return {"users_processed": len(users), "results": results}
