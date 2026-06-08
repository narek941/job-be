"""Apply to a job.

Email transport is *stubbed* for now: `apply_to_job` always writes a row to
the `applies` table with status='queued' (or 'deep_link' when no recruiter
email is known). A real transport (Resend / SMTP / etc.) plugs into
`_send_email` later — everything else stays the same.

Calling code should treat the return value as authoritative for what to tell
the user: either "queued for send" or "no email, here's a deep link".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from armapply import db

log = logging.getLogger(__name__)


ApplyOutcome = Literal["queued", "deep_link"]


@dataclass(frozen=True, slots=True)
class ApplyResult:
    outcome: ApplyOutcome
    apply_id: int
    to_email: str | None
    subject: str
    body: str


# ---------------------------------------------------------------------------
# Subject / body assembly
# ---------------------------------------------------------------------------

def _subject(job: db.Job) -> str:
    title = (job["title"] or "Application").strip()
    company = (job["company"] or "").strip()
    return f"Application: {title}" + (f" — {company}" if company else "")


def _body(job: db.Job, cover_letter: str, applicant_email: str | None) -> str:
    parts = [cover_letter.strip()]
    if applicant_email:
        parts.append(f"\n\nBest regards,\n{applicant_email}")
    parts.append(f"\n\n— Application sent regarding: {job['url']}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Transport (stub)
# ---------------------------------------------------------------------------

def _send_email(
    *,
    to_email: str,
    reply_to: str | None,
    subject: str,
    body: str,
    cv_pdf: bytes | None,
    cv_filename: str | None,
) -> None:
    """Real send wired in later. Today: log + raise NotImplementedError so we
    never silently 'succeed' against /dev/null."""
    log.info(
        "STUB email | to=%s reply_to=%s subj=%r body_len=%d cv=%s",
        to_email, reply_to, subject, len(body),
        f"{cv_filename} ({len(cv_pdf) if cv_pdf else 0} bytes)" if cv_pdf else "none",
    )
    raise NotImplementedError("Email transport not yet wired. Apply row will stay 'queued'.")


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

    subject = _subject(job)
    body = _body(job, job["cover_letter"], user["email"])
    to_email = job["recruiter_email"]
    outcome: ApplyOutcome = "queued" if to_email else "deep_link"

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
            user["cv_pdf"] if to_email else None,
            outcome,
        ),
        fetch="one",
    )
    assert row is not None
    apply_id = int(row["id"])

    if outcome == "queued":
        try:
            _send_email(
                to_email=to_email or "",
                reply_to=user["email"],
                subject=subject,
                body=body,
                cv_pdf=user["cv_pdf"],
                cv_filename=user["cv_pdf_filename"],
            )
        except NotImplementedError:
            # Stay queued; transport will pick it up once wired.
            log.info("apply %d queued (transport stub)", apply_id)
        except Exception as e:
            db.query(
                "UPDATE applies SET status = 'failed', error = %s WHERE id = %s",
                (str(e)[:1000], apply_id),
            )
            db.update_job(job["id"], status="failed", apply_error=str(e)[:500])
            raise
        else:
            db.query(
                "UPDATE applies SET status = 'sent', sent_at = NOW() WHERE id = %s",
                (apply_id,),
            )
            db.update_job(job["id"], status="applied", applied_at=db.utcnow())
            return ApplyResult(outcome="queued", apply_id=apply_id, to_email=to_email,
                               subject=subject, body=body)

        # Still queued (stub). Mark the job as 'applied' optimistically so the
        # user can move on; once real transport lands we'll demote to a real send.
        db.update_job(job["id"], status="applied", applied_at=db.utcnow())
        return ApplyResult(outcome="queued", apply_id=apply_id, to_email=to_email,
                           subject=subject, body=body)

    # No recruiter email — record as deep_link, leave job notified so the user
    # can still hit "Apply" from the bot (we'll respond with the URL).
    db.update_job(job["id"], status="applied", applied_at=db.utcnow())
    return ApplyResult(outcome="deep_link", apply_id=apply_id, to_email=None,
                       subject=subject, body=body)
