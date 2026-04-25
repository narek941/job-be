"""Standalone job scoring: LLM-powered evaluation of candidate-job fit.

Scores jobs on a 1–10 scale by comparing the user's resume against each
job description. Self-contained — uses armapply.llm_client directly.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from armapply.llm_client import get_client
from armapply.users_db import (
    _exec,
    get_user_by_id,
    update_job_field,
)

log = logging.getLogger(__name__)

# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT = """You are a job fit evaluator. Given a candidate's resume and a job description, score how well the candidate fits the role.

SCORING CRITERIA:
- 9-10: Perfect match. Candidate has direct experience in nearly all required skills and qualifications.
- 7-8: Strong match. Candidate has most required skills, minor gaps easily bridged.
- 5-6: Moderate match. Candidate has some relevant skills but missing key requirements.
- 3-4: Weak match. Significant skill gaps, would need substantial ramp-up.
- 1-2: Poor match. Completely different field or experience level.

IMPORTANT FACTORS:
- Weight technical skills heavily (programming languages, frameworks, tools)
- Consider transferable experience (automation, scripting, API work)
- Factor in the candidate's project experience
- Be realistic about experience level vs. job requirements (years of experience, seniority)

RESPOND IN EXACTLY THIS FORMAT (no other text):
SCORE: [1-10]
KEYWORDS: [comma-separated ATS keywords from the job description that match or could match the candidate]
REASONING: [2-3 sentences explaining the score]"""


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data."""
    score = 0
    keywords = ""
    reasoning = response

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                score = int(re.search(r"\d+", line).group())
                score = max(1, min(10, score))
            except (AttributeError, ValueError):
                score = 0
        elif line.startswith("KEYWORDS:"):
            keywords = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()

    return {"score": score, "keywords": keywords, "reasoning": reasoning}


def score_single_job(resume_text: str, job: dict) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        {"score": int, "keywords": str, "reasoning": str}
    """
    job_text = (
        f"TITLE: {job.get('title', 'N/A')}\n"
        f"COMPANY: {job.get('site', 'N/A')}\n"
        f"LOCATION: {job.get('location', 'N/A')}\n\n"
        f"DESCRIPTION:\n{(job.get('full_description') or job.get('description') or '')[:6000]}"
    )

    messages = [
        {"role": "system", "content": SCORE_PROMPT},
        {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
    ]

    try:
        client = get_client()
        response = client.chat(messages, max_tokens=512, temperature=0.2)
        return _parse_score_response(response)
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", job.get("title", "?"), e)
        return {"score": 0, "keywords": "", "reasoning": f"LLM error: {e}"}


def score_jobs_for_user(user_id: int, limit: int = 20) -> dict:
    """Score unscored jobs for a user that have full descriptions.

    Fetches the user's resume from the database, then scores each
    unscored job with a full description.

    Returns:
        {"scored": int, "errors": int, "elapsed": float}
    """
    # Get user's resume
    from armapply.users_db import get_user_resume
    resume_text = get_user_resume(user_id)
    if not resume_text:
        # Fallback to cv_template if no uploaded resume
        try:
            from armapply.cv_template import render_cv_text
            resume_text = render_cv_text()
        except Exception:
            log.error("No resume found for user %d", user_id)
            return {"scored": 0, "errors": 0, "elapsed": 0.0}

    # Fetch unscored jobs with descriptions
    rows = _exec(
        "SELECT url, title, site, location, description, full_description "
        "FROM jobs WHERE user_id = %s AND fit_score IS NULL "
        "AND (full_description IS NOT NULL OR (description IS NOT NULL AND LENGTH(description) > 100)) "
        "ORDER BY discovered_at DESC LIMIT %s",
        (user_id, limit), fetch="all"
    )
    if not rows:
        log.info("No unscored jobs for user %d", user_id)
        return {"scored": 0, "errors": 0, "elapsed": 0.0}

    log.info("Scoring %d jobs for user %d...", len(rows), user_id)
    t0 = time.time()
    scored = 0
    errors = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        job = dict(row)
        result = score_single_job(resume_text, job)

        if result["score"] == 0:
            errors += 1
        else:
            scored += 1

        update_job_field(user_id, job["url"], "fit_score", result["score"])
        update_job_field(user_id, job["url"], "score_reasoning", f"{result['keywords']}\n{result['reasoning']}")
        update_job_field(user_id, job["url"], "scored_at", now)

        log.info(
            "[User %d] [%d/%d] score=%d  %s",
            user_id, scored + errors, len(rows), result["score"],
            job.get("title", "?")[:60],
        )

    elapsed = time.time() - t0
    log.info("Scored %d jobs in %.1fs for user %d (%d errors)", scored, elapsed, user_id, errors)
    return {"scored": scored, "errors": errors, "elapsed": elapsed}


def score_one_job_for_user(user_id: int, url: str) -> dict:
    """Score a single specific job for a user. Returns score result."""
    from armapply.users_db import get_job_by_url, get_user_resume

    job = get_job_by_url(user_id, url)
    if not job:
        return {"ok": False, "error": "job_not_found"}
    if not job.get("full_description") and not (job.get("description") and len(job.get("description", "")) > 100):
        return {"ok": False, "error": "not_enriched"}

    resume_text = get_user_resume(user_id)
    if not resume_text:
        try:
            from armapply.cv_template import render_cv_text
            resume_text = render_cv_text()
        except Exception:
            return {"ok": False, "error": "no_resume"}

    result = score_single_job(resume_text, dict(job))
    now = datetime.now(timezone.utc).isoformat()
    update_job_field(user_id, url, "fit_score", result["score"])
    update_job_field(user_id, url, "score_reasoning", f"{result['keywords']}\n{result['reasoning']}")
    update_job_field(user_id, url, "scored_at", now)

    return {"ok": True, "score": result["score"], "reasoning": result["reasoning"], "keywords": result["keywords"]}
