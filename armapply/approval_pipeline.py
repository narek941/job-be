"""
ArmApply Approval Pipeline
==========================
Flow:
  1. Score jobs → find top candidates (fit_score >= threshold)
  2. Tailor CV PDF  +  generate cover letter for each
  3. Send Telegram message with inline ✅ Approve / ❌ Skip buttons
  4. When user clicks Approve → send via Gmail

Webhook endpoint: POST /telegram/webhook  (register with setWebhook)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from armapply.config import TELEGRAM_BOT_TOKEN
from armapply.telegram_notify import send_telegram_message


def _get_token(user_id: int) -> str:
    """Fetch user-specific bot token from Supabase prefs or fallback."""
    from armapply.users_db import get_user_preferences
    prefs = get_user_preferences(user_id)
    return prefs.get("telegram_bot_token") or TELEGRAM_BOT_TOKEN

log = logging.getLogger(__name__)

# ── Gmail credentials (OAuth2 or App Password) ─────────────────────────────
GMAIL_SENDER      = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# ── In-memory pending approvals  {callback_data_key: approval_record} ──────
# For production, replace with Redis / DB table.
_PENDING: dict[str, dict] = {}


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1:  Pick top jobs for a user
# ═══════════════════════════════════════════════════════════════════════════

def get_top_jobs(user_id: int, limit: int = 5, min_score: int = 7) -> list[dict]:
    """Return up to `limit` scored, un-applied jobs above min_score."""
    from armapply.users_db import get_jobs_for_user
    rows = get_jobs_for_user(user_id, limit=200, min_score=min_score)
    # Exclude already applied / already sent for approval
    filtered = [
        r for r in rows
        if not r.get("applied_at")
        and r.get("fit_score") is not None
        and r.get("full_description")
    ]
    # Sort descending by score
    filtered.sort(key=lambda r: (r.get("fit_score") or 0), reverse=True)
    return filtered[:limit]


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2:  Tailor CV + Cover Letter, return file paths
# ═══════════════════════════════════════════════════════════════════════════

def prepare_application_assets(user_id: int, job: dict) -> dict:
    """
    Generate tailored resume + cover letter for a job.
    Uses cv_template as the base — no full enrichment needed.
    Returns paths to text/PDF files.
    """
    import re
    from datetime import datetime, timezone
    from pathlib import Path
    from armapply.llm_features import generate_tailored_resume_text, generate_tailored_cover_letter
    from armapply.workspace import ensure_user_workspace

    root = ensure_user_workspace(user_id)
    safe_title = re.sub(r"[^\w\s-]", "", job.get("title") or "job")[:50].strip().replace(" ", "_")
    safe_site  = re.sub(r"[^\w\s-]", "", job.get("site")  or "site")[:20].strip().replace(" ", "_")
    prefix = f"{safe_site}_{safe_title}"

    tailored_dir = root / "tailored"
    cover_dir    = root / "cover_letters"
    tailored_dir.mkdir(parents=True, exist_ok=True)
    cover_dir.mkdir(parents=True, exist_ok=True)

    # 1. Tailored CV text
    cv_text = generate_tailored_resume_text(job)
    cv_txt_path = tailored_dir / f"{prefix}.txt"
    cv_txt_path.write_text(cv_text, encoding="utf-8")

    # 2. Cover letter text
    cl_text = generate_tailored_cover_letter(job, language="en")
    cl_txt_path = cover_dir / f"{prefix}_CL.txt"
    cl_txt_path.write_text(cl_text, encoding="utf-8")

    # 3. Try to convert to PDF
    cv_pdf_path = cl_pdf_path = None
    try:
        from applypilot.scoring.pdf import convert_to_pdf
        cv_pdf_path = str(convert_to_pdf(cv_txt_path))
    except Exception as e:
        log.debug("CV PDF conversion failed: %s", e)
    try:
        from applypilot.scoring.pdf import convert_to_pdf
        cl_pdf_path = str(convert_to_pdf(cl_txt_path))
    except Exception as e:
        log.debug("Cover PDF conversion failed: %s", e)

    # 4. Persist paths and TEXT content to DB
    from armapply.users_db import update_job_field, save_job_documents
    now = datetime.now(timezone.utc).isoformat()
    update_job_field(user_id, job["url"], "tailored_resume_path", str(cv_txt_path))
    update_job_field(user_id, job["url"], "cover_letter_path",    str(cl_txt_path))
    update_job_field(user_id, job["url"], "tailored_at",           now)

    # NEW: Store actual text in Supabase
    save_job_documents(user_id, job["url"], cv_text, cl_text)

    return {
        "user_id":   user_id,
        "job_url":   job["url"],
        "cv_txt":    str(cv_txt_path),
        "cv_pdf":    cv_pdf_path,
        "cover_txt": str(cl_txt_path),
        "cover_pdf": cl_pdf_path,
        "tailor_ok": True,
    }


# ═══════════════════════════════════════════════════════════════════════════
# STEP 3:  Send Telegram approval request
# ═══════════════════════════════════════════════════════════════════════════

def send_approval_request(
    chat_id: str,
    user_id: int,
    job: dict,
    assets: dict,
    recipient_email: str | None = None,
) -> str | None:
    """
    Send job preview + PDF files to Telegram with Approve / Skip buttons.
    Returns the callback_key stored in _PENDING.
    """
    token = _get_token(user_id)
    if not token or not chat_id:
        log.warning("Telegram not configured for user %d; skipping approval request.", user_id)
        return None

    title     = job.get("title") or "Untitled"
    site      = job.get("site") or "unknown"
    score     = job.get("fit_score") or 0
    url       = job.get("url", "")
    reasoning = (job.get("score_reasoning") or "")[:600]

    # Build a unique callback key
    ts  = int(datetime.now(timezone.utc).timestamp())
    key = f"approve_{user_id}_{ts}"

    # Store approval record
    _PENDING[key] = {
        "user_id":         user_id,
        "job_url":         url,
        "job_title":       title,
        "chat_id":         chat_id,
        "recipient_email": recipient_email or "",
        "assets":          assets,
        "created_at":      ts,
    }

    message = (
        f"🎯 *New Job Match — {int(score * 10)}% fit*\n\n"
        f"*{title}*  |  _{site}_\n"
        f"🔗 {url}\n\n"
        f"📊 *Why it matches:*\n{reasoning}\n\n"
        f"CV + Cover Letter are ready.\n"
        f"Should I send your application via Gmail?"
    )

    inline_keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve & Send", "callback_data": f"approve:{key}"},
            {"text": "❌ Skip",           "callback_data": f"skip:{key}"},
        ]]
    }

    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    # 1. Send text message with buttons
    try:
        r = httpx.post(f"{base}/sendMessage", json={
            "chat_id":      chat_id,
            "text":         message[:4096],
            "parse_mode":   "Markdown",
            "reply_markup": inline_keyboard,
        }, timeout=15)
        r.raise_for_status()
        log.info("Approval request sent for key=%s", key)
    except Exception as e:
        log.error("Failed to send approval message: %s", e)
        return None

    # 2. Attach CV PDF if available
    _send_document_if_exists(chat_id, assets.get("cv_pdf"),    "📄 Tailored CV",    assets=assets, doc_type="cv", bot_token=token)
    _send_document_if_exists(chat_id, assets.get("cover_pdf"), "✉️ Cover Letter", assets=assets, doc_type="cover", bot_token=token)

    return key


def _send_document_if_exists(chat_id: str, path: str | None, caption: str, assets: dict | None = None, doc_type: str = "cv", bot_token: str | None = None) -> None:
    """Send document, fall back to Supabase text if file is missing."""
    import tempfile
    from armapply.users_db import get_job_documents
    from applypilot.scoring.pdf import convert_to_pdf

    effective_path = path

    # Fallback: check Supabase if local file is missing
    if (not effective_path or not Path(effective_path).exists()) and assets:
        uid = assets.get("user_id")
        url = assets.get("job_url")
        if uid and url:
            docs = get_job_documents(uid, url)
            text = docs.get("cv_text" if doc_type == "cv" else "cover_text")
            if text:
                try:
                    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as tmp:
                        tmp.write(text)
                        tmp_path = tmp.name
                    effective_path = str(convert_to_pdf(Path(tmp_path)))
                    log.info("Regenerated PDF from Supabase for %s", caption)
                except Exception as e:
                    log.warning("Could not regenerate PDF from Supabase: %s", e)

    if not effective_path or not Path(effective_path).exists():
        return

    base = f"https://api.telegram.org/bot{bot_token or TELEGRAM_BOT_TOKEN}"
    try:
        with open(effective_path, "rb") as f:
            httpx.post(f"{base}/sendDocument", data={
                "chat_id": chat_id,
                "caption": caption,
            }, files={"document": (Path(effective_path).name, f, "application/pdf")},
            timeout=30)
    except Exception as e:
        log.warning("Could not send document %s: %s", effective_path, e)


# ═══════════════════════════════════════════════════════════════════════════
# STEP 4:  Handle Telegram webhook callback
# ═══════════════════════════════════════════════════════════════════════════

def handle_telegram_callback(update: dict, user_id: int | None = None) -> None:
    """Called by the /telegram/webhook endpoint."""
    from armapply.users_db import update_user_telegram

    # 1. Handle regular messages (to capture chat_id)
    msg = update.get("message")
    if msg and user_id:
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id:
            update_user_telegram(user_id, chat_id)
            log.info("Captured telegram_chat_id %s for user %d", chat_id, user_id)
            # Optional: send a greeting
            token = _get_token(user_id)
            try:
                base = f"https://api.telegram.org/bot{token}"
                httpx.post(f"{base}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "✅ Bot connected! You will receive new job matches here.",
                    "parse_mode": "Markdown"
                }, timeout=10)
            except Exception:
                pass

    # 2. Handle button clicks
    cb = update.get("callback_query")
    if not cb:
        return

    callback_data = cb.get("data", "")
    message_id    = cb.get("message", {}).get("message_id")
    chat_id       = str(cb.get("message", {}).get("chat", {}).get("id", ""))

    # Find the record to get the user_id if not provided
    record = None
    if callback_data.startswith(("approve:", "skip:")):
        key = callback_data.split(":", 1)[1]
        record = _PENDING.get(key)
    
    effective_uid = user_id or (record["user_id"] if record else None)
    token = _get_token(effective_uid) if effective_uid else TELEGRAM_BOT_TOKEN

    _answer_callback(cb["id"], bot_token=token)

    if callback_data.startswith("approve:"):
        key = callback_data.split(":", 1)[1]
        record = _PENDING.pop(key, None)
        if record:
            _edit_message(chat_id, message_id, "⏳ Sending application via Gmail…", bot_token=token)
            success = send_via_gmail(record)
            if success:
                _edit_message(chat_id, message_id,
                    f"✅ Application sent for *{record['job_title']}*!", bot_token=token)
                # Mark job as applied in DB
                _mark_applied(record["user_id"], record["job_url"])
            else:
                _edit_message(chat_id, message_id,
                    "❌ Gmail send failed. Check GMAIL_SENDER & GMAIL_APP_PASSWORD env vars.", bot_token=token)
        else:
            _edit_message(chat_id, message_id, "⚠️ Approval already processed or expired.", bot_token=token)

    elif callback_data.startswith("skip:"):
        key = callback_data.split(":", 1)[1]
        _PENDING.pop(key, None)
        _edit_message(chat_id, message_id, "⏭️ Job skipped.", bot_token=token)


def _answer_callback(callback_query_id: str, bot_token: str | None = None) -> None:
    token = bot_token or TELEGRAM_BOT_TOKEN
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                   json={"callback_query_id": callback_query_id}, timeout=5)
    except Exception:
        pass


def _edit_message(chat_id: str, message_id: int | None, text: str, bot_token: str | None = None) -> None:
    token = bot_token or TELEGRAM_BOT_TOKEN
    if not message_id:
        send_telegram_message(chat_id, text, bot_token=token)
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/editMessageText", json={
            "chat_id":    chat_id,
            "message_id": message_id,
            "text":       text[:4096],
            "parse_mode": "Markdown",
        }, timeout=10)
    except Exception as e:
        log.warning("editMessageText failed: %s", e)


def _mark_applied(user_id: int, url: str) -> None:
    from armapply.users_db import update_job_field
    now = datetime.now(timezone.utc).isoformat()
    update_job_field(user_id, url, "applied_at", now)
    update_job_field(user_id, url, "apply_status", "sent_via_gmail")


# ═══════════════════════════════════════════════════════════════════════════
# STEP 5:  Gmail send
# ═══════════════════════════════════════════════════════════════════════════

def send_via_gmail(record: dict) -> bool:
    """
    Send CV + cover letter as email attachments via Gmail SMTP.
    Uses App Password (no OAuth needed if 2FA is on and app password is generated).
    """
    import smtplib
    from email.message import EmailMessage

    recipient = record.get("recipient_email", "").strip()
    if not recipient:
        log.error("No recipient_email in approval record — cannot send Gmail.")
        return False
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        log.error("GMAIL_SENDER or GMAIL_APP_PASSWORD not set.")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"Application: {record['job_title']}"
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = recipient

    assets = record.get("assets", {})
    cv_txt_path    = assets.get("cv_txt")
    cover_txt_path = assets.get("cover_txt")

    # Build body from cover letter text (fallback to Supabase)
    cover_body = ""
    if cover_txt_path and Path(cover_txt_path).exists():
        cover_body = Path(cover_txt_path).read_text(encoding="utf-8")
    
    if not cover_body:
        from armapply.users_db import get_job_documents
        docs = get_job_documents(record["user_id"], record["job_url"])
        cover_body = docs.get("cover_text", "")

    msg.set_content(cover_body or f"Please find my application for the {record['job_title']} position.")

    # Attach CV PDF
    _attach_pdf(msg, assets.get("cv_pdf"),    f"CV_{record['job_title'][:40]}.pdf", assets=assets, doc_type="cv")
    _attach_pdf(msg, assets.get("cover_pdf"), f"CoverLetter_{record['job_title'][:40]}.pdf", assets=assets, doc_type="cover")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        log.info("Gmail sent to %s for job %s", recipient, record["job_title"])
        return True
    except Exception as e:
        log.error("Gmail SMTP failed: %s", e)
        return False


def _attach_pdf(msg: "EmailMessage", path: str | None, filename: str, assets: dict | None = None, doc_type: str = "cv") -> None:
    """Attach PDF, fall back to Supabase text if file is missing."""
    import tempfile
    from armapply.users_db import get_job_documents
    from applypilot.scoring.pdf import convert_to_pdf

    effective_path = path

    # Fallback: check Supabase if local file is missing
    if (not effective_path or not Path(effective_path).exists()) and assets:
        uid = assets.get("user_id")
        url = assets.get("job_url")
        if uid and url:
            docs = get_job_documents(uid, url)
            text = docs.get("cv_text" if doc_type == "cv" else "cover_text")
            if text:
                try:
                    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as tmp:
                        tmp.write(text)
                        tmp_path = tmp.name
                    effective_path = str(convert_to_pdf(Path(tmp_path)))
                    log.info("Regenerated attachment %s from Supabase", filename)
                except Exception as e:
                    log.warning("Could not regenerate attachment %s from Supabase: %s", filename, e)

    if not effective_path or not Path(effective_path).exists():
        return
    try:
        data = Path(effective_path).read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=filename)
    except Exception as e:
        log.warning("Could not attach %s: %s", filename, e)


# ═══════════════════════════════════════════════════════════════════════════
# HIGH-LEVEL: run the full pipeline for one user
# ═══════════════════════════════════════════════════════════════════════════

def run_approval_pipeline(
    user_id: int,
    chat_id: str,
    recipient_email: str,
    min_score: int = 7,
    max_jobs: int = 3,
) -> dict:
    """
    Main entry point — called by the background scheduler or an API endpoint.
    Returns a summary dict.
    """
    results = []

    top_jobs = get_top_jobs(user_id, limit=max_jobs, min_score=min_score)
    if not top_jobs:
        log.info("No suitable jobs found for user %d", user_id)
        return {"sent": 0, "skipped": 0, "reason": "no_suitable_jobs"}

    for job in top_jobs:
        try:
            assets = prepare_application_assets(user_id, job)
            key    = send_approval_request(chat_id, user_id, job, assets, recipient_email)
            results.append({"job": job.get("title"), "key": key, "ok": key is not None})
        except Exception as e:
            log.error("Approval pipeline error for job %s: %s", job.get("url"), e)
            results.append({"job": job.get("title"), "ok": False, "error": str(e)})

    sent = sum(1 for r in results if r.get("ok"))
    return {"sent": sent, "total": len(top_jobs), "results": results}
