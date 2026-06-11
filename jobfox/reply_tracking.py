"""Reply tracking — funnel Levels 2+3.

For every apply with a known recipient, search the user's Gmail for a
message FROM that address newer than the application. A hit means the
recruiter answered: flip the job to `replied`, record a `reply_received`
event, classify the reply with the LLM (interview / offer / rejection),
and ping the user in Telegram.

Requires the `gmail.readonly` grant. Users who connected before that
scope was added are skipped quietly (403 from Google) until they re-run
/connect_gmail — surfaced once via a Telegram nudge.

Triggered by POST /cron/replies (hourly, same shared secret as /cron) and
opportunistically at the end of each daily pipeline run.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from jobfox import analytics, db, gmail_api, llm, telegram_api

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gmail message helpers (pure, unit-testable)
# ---------------------------------------------------------------------------

def gmail_search_query(to_email: str, applied_at) -> str:
    """Search for messages FROM the recruiter after the application moment.

    Gmail's `after:` accepts a unix timestamp. Subtracting nothing is
    correct: their reply necessarily postdates our send."""
    return f"from:{to_email} after:{int(applied_at.timestamp())}"


def message_text(payload: dict[str, Any]) -> str:
    """Best-effort plain text from a Gmail `users.messages.get` payload.

    Walks multipart trees depth-first preferring text/plain, falls back to
    text/html (tags crudely stripped) — recruiters' mail is usually both."""
    def _decode(body: dict[str, Any]) -> str:
        data = body.get("data") or ""
        if not data:
            return ""
        try:
            return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
        except Exception:
            return ""

    def _walk(part: dict[str, Any], want: str) -> str:
        if part.get("mimeType") == want:
            return _decode(part.get("body") or {})
        for child in part.get("parts") or []:
            found = _walk(child, want)
            if found:
                return found
        return ""

    text = _walk(payload, "text/plain")
    if not text:
        html = _walk(payload, "text/html")
        if html:
            import re

            text = re.sub(r"<[^>]+>", " ", html)
    return text.strip()


# ---------------------------------------------------------------------------
# LLM classification (Level 3)
# ---------------------------------------------------------------------------

_CLASSIFY_SYSTEM = (
    "You classify a reply that a job applicant received from a company. "
    "Output strict JSON: {\"type\": one of \"interview\" (they want to "
    "schedule or conduct an interview/call/test task), \"offer\" (a job "
    "offer is being made), \"rejection\" (the application is declined), "
    "\"reply\" (a human answered but none of the above — questions, "
    "acknowledgement, request for documents), \"other\" (autoreply, "
    "out-of-office, newsletter, wrong thread); "
    "\"interview_datetime\": ISO 8601 string if a specific interview "
    "date/time is proposed, else null; "
    "\"summary\": one short sentence in plain English}."
)

# classification type -> (job status, event type) — `reply`/`other` keep the
# job at `replied` with no extra event.
_TYPE_ACTIONS: dict[str, tuple[str, str]] = {
    "interview": ("interview", "interview"),
    "offer": ("offer", "offer"),
    "rejection": ("rejected", "rejected"),
}


def classify_reply(text: str) -> dict[str, Any]:
    """LLM-classify a recruiter reply. Falls back to plain 'reply' on any
    model failure — detection (Level 2) must not break on classification."""
    try:
        data = llm.complete_json(
            system=_CLASSIFY_SYSTEM,
            user=f"Reply received:\n\n{text[:6000]}",
            temperature=0.0,
            max_tokens=512,
        )
        if not isinstance(data, dict):
            raise llm.LLMError("expected object")
    except Exception as e:
        log.warning("reply classification failed: %s", e)
        return {"type": "reply", "interview_datetime": None, "summary": ""}
    rtype = str(data.get("type") or "reply").lower()
    if rtype not in ("interview", "offer", "rejection", "reply", "other"):
        rtype = "reply"
    dt = data.get("interview_datetime")
    return {
        "type": rtype,
        "interview_datetime": str(dt) if dt else None,
        "summary": str(data.get("summary") or "").strip()[:300],
    }


def _notification(company: str, classification: dict[str, Any]) -> str:
    c = company or "The company"
    rtype = classification["type"]
    summary = classification.get("summary") or ""
    when = classification.get("interview_datetime")
    if rtype == "interview":
        line = f"🎤 *{c}* wants an interview!"
        if when:
            line += f"\n📅 Proposed: {when}"
    elif rtype == "offer":
        line = f"🎉 *{c}* sent you an offer!"
    elif rtype == "rejection":
        line = f"😔 *{c}* declined this one. Onwards — the fox keeps hunting."
    else:
        line = f"📬 *{c}* replied to your application!"
    if summary:
        line += f"\n_{summary}_"
    return line


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------

def poll_user(user: db.User) -> dict[str, int]:
    """Check pending applies for one user. Returns counters."""
    counts = {"checked": 0, "replies": 0, "skipped_no_scope": 0}
    pending = db.list_applies_awaiting_reply(user["id"])
    if not pending:
        return counts

    try:
        creds = gmail_api._credentials(user["gmail_refresh_token"] or "")
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    except gmail_api.GmailReauthRequired:
        log.info("reply poll: user %d needs reauth", user["id"])
        return counts
    except Exception as e:
        log.warning("reply poll: gmail client failed user=%d: %s", user["id"], e)
        return counts

    for ap in pending:
        counts["checked"] += 1
        q = gmail_search_query(ap["to_email"], ap["created_at"])
        try:
            resp = (
                service.users().messages()
                .list(userId="me", q=q, maxResults=1)
                .execute()
            )
        except HttpError as e:
            if e.resp.status == 403:
                # Granted before gmail.readonly existed — nudge once and stop.
                counts["skipped_no_scope"] = 1
                _nudge_rescope(user)
                break
            log.warning("reply poll: search failed apply=%d: %s", ap["apply_id"], e)
            continue

        msgs = resp.get("messages") or []
        if not msgs:
            continue

        msg_id = str(msgs[0]["id"])
        try:
            msg = (
                service.users().messages()
                .get(userId="me", id=msg_id, format="full")
                .execute()
            )
            text = message_text(msg.get("payload") or {}) or str(msg.get("snippet") or "")
        except Exception as e:
            log.warning("reply poll: fetch failed msg=%s: %s", msg_id, e)
            text = ""

        _record_reply(user, ap, msg_id, text)
        counts["replies"] += 1

    return counts


def _record_reply(user: db.User, ap: dict[str, Any], msg_id: str, text: str) -> None:
    db.mark_apply_replied(ap["apply_id"], msg_id)
    db.update_job(ap["job_id"], status="replied")
    db.add_event(
        ap["job_id"], user["id"], "reply_received",
        {"from": ap["to_email"], "msg_id": msg_id},
    )

    classification = classify_reply(text) if text else {
        "type": "reply", "interview_datetime": None, "summary": "",
    }
    action = _TYPE_ACTIONS.get(classification["type"])
    if action:
        status, event_type = action
        db.update_job(ap["job_id"], status=status)
        db.add_event(
            ap["job_id"], user["id"], event_type,  # type: ignore[arg-type]
            {
                "auto": True,
                "summary": classification.get("summary"),
                "interview_datetime": classification.get("interview_datetime"),
            },
        )
    analytics.track(
        user["id"], "reply_detected",
        {"type": classification["type"], "company": ap.get("company")},
    )

    try:
        telegram_api.send_message(
            user["tg_chat_id"],
            _notification(ap.get("company") or "", classification)
            + f"\n\n💼 {ap.get('title') or 'your application'}",
            parse_mode="Markdown",
        )
    except Exception:
        log.exception("reply notification failed user=%d", user["id"])


_RESCOPE_NUDGED: set[int] = set()  # once per process — avoids hourly spam


def _nudge_rescope(user: db.User) -> None:
    if user["id"] in _RESCOPE_NUDGED:
        return
    _RESCOPE_NUDGED.add(user["id"])
    try:
        telegram_api.send_message(
            user["tg_chat_id"],
            "📬 JobFox can now detect recruiter replies automatically and "
            "update your funnel. Re-run /connect\\_gmail once to enable it.",
            parse_mode="Markdown",
        )
    except Exception:
        log.exception("rescope nudge failed user=%d", user["id"])


def run_all() -> dict[str, int]:
    """Poll every eligible user. Called hourly via /cron/replies."""
    totals = {"users": 0, "checked": 0, "replies": 0}
    for user in db.list_users_with_gmail():
        totals["users"] += 1
        try:
            c = poll_user(user)
            totals["checked"] += c["checked"]
            totals["replies"] += c["replies"]
        except Exception:
            log.exception("reply poll failed user=%d", user["id"])
        db.log_run(user["id"], "reply_poll", "ok", "")
    log.info("reply poll: %s", totals)
    return totals
