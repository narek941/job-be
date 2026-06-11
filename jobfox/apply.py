"""Apply to a job.

Three transports, picked in priority order per call:
  1. **Gmail Drafts API** (preferred) — user ran /connect_gmail, so we
     drop a real draft into their account with the CV attached. They
     review + send from their own Gmail.
  2. **SMTP** — legacy single-bot-address path. Fires the email
     immediately if GMAIL_ADDRESS/GMAIL_APP_PASSWORD are set.
  3. **deep_link** — fallback when neither is configured or recipient
     unknown. Bot hands the user a pre-filled Gmail compose URL + the
     CV as a Telegram document.

Calling code should treat the `outcome` field as authoritative for what
to tell the user.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal

from jobfox import db, gmail_api
from jobfox.config import settings
from jobfox.gmail_api import web_compose_url as gmail_compose_url

log = logging.getLogger(__name__)


ApplyOutcome = Literal["sent", "deep_link", "gmail_draft"]


@dataclass(frozen=True, slots=True)
class ApplyResult:
    outcome: ApplyOutcome
    apply_id: int
    to_email: str | None
    subject: str
    body: str
    # Populated only when outcome == "gmail_draft" — used by the bot to
    # link the user straight to the right Gmail account's Drafts folder.
    gmail_draft_id: str | None = None
    gmail_address: str | None = None
    # Set when the user's Gmail refresh token was rejected and we fell back
    # to deep_link. Bot prompts the user to /connect_gmail.
    needs_gmail_reauth: bool = False


# ---------------------------------------------------------------------------
# Subject / body assembly
# ---------------------------------------------------------------------------

def _subject(job: db.Job) -> str:
    title = (job["title"] or "Application").strip()
    company = (job["company"] or "").strip()
    return f"Application: {title}" + (f" — {company}" if company else "")


def _salutation(job: db.Job) -> str:
    """A salutation that uses the company name when we have one — softer than
    'Dear Hiring Manager' and avoids the generic recruiter-spam smell."""
    company = (job["company"] or "").strip()
    if company:
        return f"Hi {company} team,"
    return "Hi there,"


def _signature(applicant_name: str | None, applicant_email: str | None) -> str:
    """Sign-off block. Name on first line (recruiter-friendly), email below."""
    lines: list[str] = ["Best,"]
    if applicant_name:
        lines.append(applicant_name.strip())
    if applicant_email:
        lines.append(applicant_email.strip())
    return "\n".join(lines)


def _body(
    job: db.Job,
    cover_letter: str,
    applicant_name: str | None,
    applicant_email: str | None,
    portfolio_links: list[str] | None = None,
) -> str:
    """Assemble the full email body: salutation + cover + sign-off + ref.

    The cover letter from the LLM has no salutation or sign-off — we own
    those bookends so wording stays consistent across applications.
    """
    parts: list[str] = [
        _salutation(job),
        "",
        cover_letter.strip(),
    ]
    if portfolio_links:
        parts += ["", "Portfolio: " + " · ".join(portfolio_links[:5])]
    parts += [
        "",
        _signature(applicant_name, applicant_email),
    ]
    parts.append("")
    parts.append(f"— Re: {job['url']}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Transport (stub)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class QuotaExceeded(Exception):
    """Weekly apply quota hit — callers show an upgrade CTA, not an error."""

    tier: str
    used: int
    limit: int

    def __str__(self) -> str:
        return f"apply quota reached: {self.used}/{self.limit} this week on '{self.tier}'"


class SmtpNotConfigured(RuntimeError):
    """Raised when SMTP credentials are missing — caller decides what to do."""


def _send_email(
    *,
    to_email: str,
    reply_to: str | None,
    subject: str,
    body: str,
    cv_pdf: bytes | None,
    cv_filename: str | None,
) -> None:
    """Send via Gmail SMTP over SSL (port 465).

    Reply-To is set to the candidate's own email when known so recruiter
    responses skip applybot's inbox and land with them directly.
    """
    s = settings()
    if not s.smtp_configured:
        raise SmtpNotConfigured(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set — auto-apply is disabled."
        )

    msg = EmailMessage()
    msg["From"] = s.gmail_address
    msg["To"] = to_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    if cv_pdf:
        msg.add_attachment(
            cv_pdf,
            maintype="application",
            subtype="pdf",
            filename=cv_filename or "cv.pdf",
        )

    log.info("SMTP send | to=%s subj=%r body_len=%d cv=%s",
             to_email, subject, len(body),
             f"{cv_filename} ({len(cv_pdf) if cv_pdf else 0} bytes)" if cv_pdf else "none")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(s.gmail_address, s.gmail_app_password)
        smtp.send_message(msg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_to_job(user: db.User, job: db.Job) -> ApplyResult:
    """Create an apply record. Sends email if a recruiter email is known and
    transport is implemented; otherwise returns a deep-link result for the
    user to apply manually.

    Pre-conditions: job already has `cover_letter`. Caller is responsible
    for ensuring that (pipeline does it).
    """
    if not job["cover_letter"]:
        raise ValueError(f"job {job['id']} has no cover letter; generate one first")

    tier = user.get("tier") or "free"
    used = db.applies_this_week(user["id"])
    limit = db.apply_quota(tier)
    if used >= limit:
        raise QuotaExceeded(tier=tier, used=used, limit=limit)

    subject = _subject(job)
    body = _body(
        job, job["cover_letter"], user["name"], user["email"],
        portfolio_links=user.get("portfolio_links"),
    )
    to_email = job["recruiter_email"]

    # Transport selection. Gmail draft beats SMTP because the draft lives
    # in the user's own account (right From:, reviewable, fewer deliver-
    # ability issues). SMTP only fires if a bot-shared inbox is configured
    # AND we have a recipient. Everything else falls through to deep_link.
    has_gmail_draft = bool(user.get("gmail_refresh_token"))
    can_smtp_send = bool(to_email) and settings().smtp_configured and not has_gmail_draft

    initial_status: str
    if has_gmail_draft:
        initial_status = "queued"
    elif can_smtp_send:
        initial_status = "queued"
    else:
        initial_status = "deep_link"

    row = db.query(
        """
        INSERT INTO applies (job_id, user_id, to_email, subject, body, cv_pdf, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            job["id"],
            user["id"],
            to_email,
            subject,
            body,
            # Persist the CV bytes on the apply row whenever a transport
            # might use them — that's "anything but pure deep_link". Lets
            # us re-create the draft later from the row alone if needed.
            user["cv_pdf"] if initial_status != "deep_link" else None,
            initial_status,
        ),
        fetch="one",
    )
    assert row is not None
    apply_id = int(row["id"])

    # --- Path 1: Gmail Drafts API (preferred when connected) ----------------
    if has_gmail_draft:
        try:
            draft_id = gmail_api.create_draft(
                refresh_token=user["gmail_refresh_token"] or "",
                from_addr=user.get("gmail_address") or user["email"] or "",
                to=to_email,
                subject=subject,
                body=body,
                cv_pdf=user["cv_pdf"],
                cv_filename=user["cv_pdf_filename"],
            )
        except gmail_api.GmailDraftError as e:
            log.warning("Gmail draft failed for user=%d job=%d: %s",
                        user["id"], job["id"], e)
            needs_reauth = isinstance(e, gmail_api.GmailReauthRequired)
            if needs_reauth:
                # Token is dead — wipe it so future applies skip the Gmail
                # path entirely instead of round-tripping to Google each time.
                db.update_user(user["id"], gmail_refresh_token=None)
            # Fall back to the deep_link path so the dealer can still act —
            # don't fail the whole apply because Gmail blipped.
            db.query(
                "UPDATE applies SET status = 'deep_link', error = %s WHERE id = %s",
                (str(e)[:1000], apply_id),
            )
            db.update_job(job["id"], status="applied", applied_at=db.utcnow())
            db.add_event(job["id"], user["id"], "applied", {"outcome": "deep_link"})
            return ApplyResult(
                outcome="deep_link", apply_id=apply_id, to_email=to_email,
                subject=subject, body=body,
                needs_gmail_reauth=needs_reauth,
            )
        db.query(
            "UPDATE applies SET status = 'sent', sent_at = NOW() WHERE id = %s",
            (apply_id,),
        )
        db.update_job(job["id"], status="applied", applied_at=db.utcnow())
        db.add_event(job["id"], user["id"], "applied", {"outcome": "gmail_draft"})
        return ApplyResult(
            outcome="gmail_draft", apply_id=apply_id, to_email=to_email,
            subject=subject, body=body,
            gmail_draft_id=draft_id,
            gmail_address=user.get("gmail_address"),
        )

    # --- Path 2: SMTP send (legacy single-inbox flow) -----------------------
    if can_smtp_send:
        try:
            _send_email(
                to_email=to_email or "",
                reply_to=user["email"] or None,
                subject=subject,
                body=body,
                cv_pdf=user["cv_pdf"],
                cv_filename=user["cv_pdf_filename"],
            )
        except SmtpNotConfigured:
            # Demote to deep_link — caller will hand back the listing URL.
            db.query(
                "UPDATE applies SET status = 'deep_link' WHERE id = %s",
                (apply_id,),
            )
            db.update_job(job["id"], status="applied", applied_at=db.utcnow())
            db.add_event(job["id"], user["id"], "applied", {"outcome": "deep_link"})
            return ApplyResult(outcome="deep_link", apply_id=apply_id, to_email=None,
                               subject=subject, body=body)
        except Exception as e:
            db.query(
                "UPDATE applies SET status = 'failed', error = %s WHERE id = %s",
                (str(e)[:1000], apply_id),
            )
            db.update_job(job["id"], status="failed", apply_error=str(e)[:500])
            raise

        db.query(
            "UPDATE applies SET status = 'sent', sent_at = NOW() WHERE id = %s",
            (apply_id,),
        )
        db.update_job(job["id"], status="applied", applied_at=db.utcnow())
        db.add_event(job["id"], user["id"], "applied", {"outcome": "sent"})
        return ApplyResult(outcome="sent", apply_id=apply_id, to_email=to_email,
                           subject=subject, body=body)

    # --- Path 3: deep_link (no transport configured / no recipient) ---------
    db.update_job(job["id"], status="applied", applied_at=db.utcnow())
    db.add_event(job["id"], user["id"], "applied", {"outcome": "deep_link"})
    return ApplyResult(outcome="deep_link", apply_id=apply_id, to_email=to_email,
                       subject=subject, body=body)
