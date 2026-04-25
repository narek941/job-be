"""Telegram notification helpers: send messages, job matches, daily summaries."""

from __future__ import annotations

import logging

import httpx

from armapply.config import TELEGRAM_BOT_TOKEN

log = logging.getLogger(__name__)


def send_telegram_message(chat_id: str, text: str, bot_token: str | None = None) -> bool:
    """Send a plain text message to a Telegram chat."""
    token = bot_token or TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = httpx.post(url, json={
            "chat_id": chat_id, 
            "text": text[:4000],
            "parse_mode": "Markdown"
        }, timeout=15.0)
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False


def send_job_match_notification(
    chat_id: str,
    job: dict,
    cover_letter_text: str | None = None,
    bot_token: str | None = None,
) -> bool:
    """Send a rich job match notification with cover letter preview."""
    title = job.get("title") or "Unknown Position"
    company = job.get("site") or "Unknown"
    score = job.get("fit_score") or "?"
    location = job.get("location") or ""
    salary = job.get("salary") or ""
    url = job.get("url") or ""
    reasoning = job.get("score_reasoning") or ""

    # Build message
    lines = [
        f"🚀 *ArmApply Match Found!*",
        f"",
        f"📋 *{title}*",
        f"🏢 {company}",
    ]
    if location:
        lines.append(f"📍 {location}")
    if salary:
        lines.append(f"💰 {salary}")
    lines.append(f"⭐ Score: *{score}/10*")

    if reasoning:
        # Take first 200 chars of reasoning
        short_reason = reasoning[:200]
        if len(reasoning) > 200:
            short_reason += "..."
        lines.append(f"")
        lines.append(f"📊 _{short_reason}_")

    if cover_letter_text:
        # Show first 300 chars of cover letter as preview
        preview = cover_letter_text[:300].replace("*", "").replace("_", "")
        if len(cover_letter_text) > 300:
            preview += "..."
        lines.append(f"")
        lines.append(f"✉️ *Cover Letter Preview:*")
        lines.append(preview)

    if url:
        lines.append(f"")
        lines.append(f"🔗 [View Job]({url})")

    lines.append(f"")
    lines.append(f"✅ Tailored cover letter generated!")

    text = "\n".join(lines)
    return send_telegram_message(chat_id, text, bot_token)


def send_daily_summary(
    chat_id: str,
    stats: dict,
    bot_token: str | None = None,
) -> bool:
    """Send a daily summary of job hunting activity."""
    new_today = stats.get("new_today", 0)
    high_scoring = stats.get("high_scoring", 0)
    total = stats.get("total", 0)

    text = (
        f"📊 *ArmApply Daily Summary*\n\n"
        f"🆕 New jobs today: *{new_today}*\n"
        f"⭐ High-scoring matches (≥7): *{high_scoring}*\n"
        f"📦 Total jobs tracked: *{total}*\n\n"
        f"Open the app to review your matches!"
    )

    return send_telegram_message(chat_id, text, bot_token)
