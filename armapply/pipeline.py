"""Daily per-user pipeline: discover → score → tailor → notify → (auto-apply).

Each stage is a single function so they can be re-run independently from a
script or future debug endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterator

from armapply import apply as apply_mod
from armapply import db, discovery, match
from armapply.bot import notify_match  # forward decl: bot.py exports this

log = logging.getLogger(__name__)


@dataclass(slots=True)
class UserPipelineResult:
    user_id: int
    discovery: dict[str, dict[str, int]] = field(default_factory=dict)
    scored: int = 0
    notified: int = 0
    auto_applied: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def _score_new_jobs(user: db.User) -> int:
    """Score every job in status='new'. Updates job.score, job.reason, and
    moves to status='scored' (or 'skipped' if the company is muted)."""
    new_jobs = db.list_new_jobs(user["id"])
    if not new_jobs:
        return 0
    muted = {c.lower() for c in (user["muted_companies"] or [])}
    cv = user["cv_text"] or ""
    scored = 0
    for job in new_jobs:
        company = (job["company"] or "").lower()
        if company and company in muted:
            db.update_job(job["id"], status="muted")
            continue
        try:
            res = match.score_job(cv, job)
        except Exception as e:
            log.warning("score failed user=%d job=%d: %s", user["id"], job["id"], e)
            db.update_job(job["id"], status="failed", apply_error=f"score: {e}"[:500])
            continue
        db.update_job(job["id"], score=res.score, reason=res.reason, status="scored")
        scored += 1
    return scored


def _generate_for_match(user: db.User, job: db.Job) -> db.Job:
    """Ensure cover_letter + cv_tweaks exist on the job. Returns the refreshed row."""
    cv = user["cv_text"] or ""
    updates: dict[str, object] = {}
    if not job["cover_letter"]:
        try:
            updates["cover_letter"] = match.cover_letter(cv, job)
        except Exception as e:
            log.warning("cover_letter failed job=%d: %s", job["id"], e)
    if not job["cv_tweaks"]:
        try:
            tweaks = match.cv_tweaks(cv, job)
            updates["cv_tweaks"] = dict(tweaks)
        except Exception as e:
            log.warning("cv_tweaks failed job=%d: %s", job["id"], e)
    if updates:
        db.update_job(job["id"], **updates)
        refreshed = db.get_job(job["id"])
        assert refreshed is not None
        return refreshed
    return job


def _candidates(user: db.User) -> Iterator[db.Job]:
    yield from db.list_jobs_to_notify(user["id"], user["min_score_notify"])


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def run_for_user(user: db.User) -> UserPipelineResult:
    result = UserPipelineResult(user_id=user["id"])

    # 1) discover
    try:
        result.discovery = discovery.discover_for_user(user)
        db.log_run(user["id"], "discover", "ok", str(result.discovery)[:1000])
    except Exception as e:
        log.exception("discover failed user=%d", user["id"])
        result.errors.append(f"discover: {e}")
        db.log_run(user["id"], "discover", "error", str(e)[:1000])

    # 2) score
    try:
        result.scored = _score_new_jobs(user)
        db.log_run(user["id"], "score", "ok", f"scored={result.scored}")
    except Exception as e:
        log.exception("score failed user=%d", user["id"])
        result.errors.append(f"score: {e}")
        db.log_run(user["id"], "score", "error", str(e)[:1000])

    # 3) tailor + notify / auto-apply
    for job in _candidates(user):
        try:
            job = _generate_for_match(user, job)
        except Exception as e:
            result.errors.append(f"tailor job={job['id']}: {e}")
            continue
        if not job["cover_letter"]:
            continue  # generation failed; nothing actionable yet

        should_auto = (
            user["auto_apply"]
            and (job["score"] or 0) >= user["min_score_auto_apply"]
            and bool(job["recruiter_email"])
        )
        if should_auto:
            try:
                apply_mod.apply_to_job(user, job)
                result.auto_applied += 1
                db.log_run(user["id"], "auto_apply", "ok", f"job={job['id']}")
            except Exception as e:
                result.errors.append(f"auto_apply job={job['id']}: {e}")
                db.log_run(user["id"], "auto_apply", "error", f"job={job['id']} {e}"[:1000])
        else:
            try:
                notify_match(user, job)
                db.update_job(job["id"], status="notified", notified_at=db.utcnow())
                result.notified += 1
            except Exception as e:
                result.errors.append(f"notify job={job['id']}: {e}")
                db.log_run(user["id"], "notify", "error", f"job={job['id']} {e}"[:1000])

    log.info(
        "user %d: scored=%d notified=%d auto=%d errors=%d",
        user["id"], result.scored, result.notified, result.auto_applied, len(result.errors),
    )
    return result


def run_all() -> list[UserPipelineResult]:
    users = db.list_active_users()
    log.info("daily pipeline: %d active users", len(users))
    return [run_for_user(u) for u in users]
