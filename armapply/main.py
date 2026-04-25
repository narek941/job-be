"""ArmApply FastAPI application — standalone (no ApplyPilot dependency)."""

from __future__ import annotations

import json
import logging
import os
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from armapply.auth_deps import create_access_token, current_user, hash_password, verify_password
from armapply.config import DATA_ROOT
from armapply.job_codec import encode_job_id, try_decode_job_id
from armapply.discovery import run_full_discovery, enrich_staffam_jobs
from armapply.scoring import score_jobs_for_user, score_one_job_for_user
from armapply.searches_build import build_search_config
from armapply.telegram_notify import send_telegram_message
from armapply.llm_features import (
    generate_interview_prep,
    generate_recruiter_reply_draft,
    generate_tailored_cover_letter,
    generate_tailored_resume_text,
    extract_profile_from_resume,
)
from armapply.calendar_ics import events_to_ics
from armapply.users_db import (
    create_calendar_event,
    create_user,
    delete_calendar_event,
    get_job_by_url,
    get_jobs_for_user,
    get_jobs_stats_supabase,
    get_user_by_email,
    get_user_by_id,
    get_user_preferences,
    get_user_resume,
    init_app_db,
    list_calendar_events,
    list_pipeline_runs,
    log_pipeline_run,
    save_job_documents,
    save_user_resume,
    update_job_field,
    update_user_preferences,
    update_user_telegram,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("armapply")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="ArmApply API", version="2.0.0", description="Standalone job discovery & AI matching platform")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    init_app_db()

    # Seed test user if configured
    try:
        from armapply.dev_seed import maybe_seed_test_user
        maybe_seed_test_user()
    except Exception:
        pass


@app.on_event("shutdown")
async def _shutdown() -> None:
    pass  # No background scheduler to stop


# --- Auth ---

class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@app.post("/auth/register", response_model=TokenOut)
@limiter.limit("5/minute")
def register(request: Request, body: RegisterBody) -> TokenOut:
    if get_user_by_email(body.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    uid = create_user(body.email, hash_password(body.password))
    token = create_access_token(body.email, uid)
    return TokenOut(access_token=token)


@app.post("/auth/login", response_model=TokenOut)
@limiter.limit("20/minute")
def login(request: Request, body: LoginBody) -> TokenOut:
    u = get_user_by_email(body.email)
    if not u or not verify_password(body.password, u["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenOut(access_token=create_access_token(body.email, u["id"]))


User = Annotated[dict, Depends(current_user)]


def _row_to_job(row: Any) -> dict[str, Any]:
    d = dict(row)
    url = d.get("url") or ""
    d["job_id"] = encode_job_id(url)
    return d


# --- Search / Discovery ---

class SearchJobsBody(BaseModel):
    queries_en: list[str] = Field(default_factory=list)
    queries_hy: list[str] = Field(default_factory=list)
    locations: list[dict[str, Any]] | None = None
    linkedin: bool = True
    staff_am: bool = True
    indeed: bool = False
    country: str = "worldwide"
    telegram_channels: list[str] | None = None


@app.post("/search-jobs")
@limiter.limit("10/hour")
async def search_jobs(request: Request, user: User, body: SearchJobsBody) -> dict[str, Any]:
    uid = user["id"]

    # Build search config and save to user preferences
    search_config = build_search_config(
        body.queries_en,
        body.queries_hy,
        body.locations,
        body.linkedin,
        body.staff_am,
        body.indeed,
        body.country,
        body.telegram_channels,
    )

    # Save search config in user preferences
    prefs = get_user_preferences(uid)
    prefs["search_config"] = search_config
    update_user_preferences(uid, prefs)

    # Run discovery
    result = run_full_discovery(uid, search_config=search_config)
    log_pipeline_run(uid, "discover", "ok", json.dumps(result, default=str)[:2000])
    return {"status": "ok", "result": result}


# --- Scoring ---

class ScoreJobsBody(BaseModel):
    limit: int = 20


@app.post("/score-jobs")
@limiter.limit("30/hour")
async def score_jobs_ep(request: Request, user: User, body: ScoreJobsBody) -> dict[str, Any]:
    uid = user["id"]
    result = score_jobs_for_user(uid, limit=body.limit)
    log_pipeline_run(uid, "score", "ok", json.dumps(result, default=str)[:2000])

    chat_id = user.get("telegram_chat_id")
    if chat_id and result.get("scored", 0) > 0:
        send_telegram_message(
            str(chat_id),
            f"ArmApply: scored {result.get('scored')} job(s). Open the app to review.",
        )
    return {"status": "ok", "result": result}


# --- Cover Letter ---

@app.post("/generate-cover-letter/{job_id}")
@limiter.limit("60/hour")
async def cover_letter_ep(
    request: Request,
    user: User,
    job_id: str,
) -> dict[str, Any]:
    uid = user["id"]
    url = try_decode_job_id(job_id)
    job = get_job_by_url(uid, url)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    cover_letter = generate_tailored_cover_letter(dict(job))

    # Save to database
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    update_job_field(uid, url, "cover_letter_text", cover_letter)
    update_job_field(uid, url, "cover_letter_at", now)
    update_job_field(uid, url, "cover_attempts", (job.get("cover_attempts") or 0) + 1)

    log_pipeline_run(uid, "cover", "ok", url[:500])
    return {"ok": True, "cover_letter_text": cover_letter}


# --- Tailored Resume ---

@app.post("/tailor-resume/{job_id}")
@limiter.limit("60/hour")
async def tailor_resume_ep(
    request: Request,
    user: User,
    job_id: str,
) -> dict[str, Any]:
    uid = user["id"]
    url = try_decode_job_id(job_id)
    job = get_job_by_url(uid, url)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    resume_text = generate_tailored_resume_text(dict(job))

    # Save to database
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    update_job_field(uid, url, "tailored_resume_text", resume_text)
    update_job_field(uid, url, "tailored_at", now)
    update_job_field(uid, url, "tailor_attempts", (job.get("tailor_attempts") or 0) + 1)

    log_pipeline_run(uid, "tailor", "ok", url[:500])
    return {"ok": True, "tailored_resume_text": resume_text}


# --- Score single job ---

@app.post("/score-job/{job_id}")
@limiter.limit("60/hour")
async def score_one_job_ep(
    request: Request,
    user: User,
    job_id: str,
) -> dict[str, Any]:
    uid = user["id"]
    url = try_decode_job_id(job_id)
    result = score_one_job_for_user(uid, url)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "scoring_failed"))
    return result


# --- Jobs CRUD ---

@app.get("/jobs")
async def list_jobs(
    user: User,
    limit: int = Query(50, ge=1, le=200),
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    rows = get_jobs_for_user(user["id"], limit=limit, min_score=min_score)
    for r in rows:
        r["job_id"] = encode_job_id(r["url"])
    return rows


@app.get("/jobs/{job_id}")
async def get_job(user: User, job_id: str) -> dict[str, Any]:
    url = try_decode_job_id(job_id)
    job = get_job_by_url(user["id"], url)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job["job_id"] = job_id
    return job


# --- Monitoring ---

@app.get("/monitoring/dashboard")
async def monitoring_dashboard(user: User) -> dict[str, Any]:
    uid = user["id"]
    stats = get_jobs_stats_supabase(uid)
    runs = list_pipeline_runs(uid, limit=100)
    return {
        "applypilot_stats": stats,
        "daily_activity": [],
        "pipeline_runs": runs,
    }


# --- Profile ---

@app.get("/profile")
async def get_profile(user: User) -> dict[str, Any]:
    uid = user["id"]
    prefs = get_user_preferences(uid)
    return prefs.get("profile_data", {})


@app.put("/profile")
async def update_profile(user: User, body: dict[str, Any]) -> dict[str, str]:
    uid = user["id"]
    prefs = get_user_preferences(uid)

    profile = prefs.get("profile_data", {})

    def deep_merge(a: dict, b: dict) -> dict:
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                deep_merge(a[k], v)
            else:
                a[k] = v
        return a

    deep_merge(profile, body)
    prefs["profile_data"] = profile
    update_user_preferences(uid, prefs)
    return {"status": "ok"}


# --- Resume Upload ---

@app.post("/profile/resume")
async def upload_resume_text(user: User, text: str = Query(..., description="Plain-text resume")) -> dict[str, str]:
    uid = user["id"]
    save_user_resume(uid, text)
    return {"status": "ok"}


@app.post("/profile/resume-pdf")
async def upload_resume_pdf(user: User, file: UploadFile = File(...)) -> dict[str, str]:
    uid = user["id"]
    data = await file.read()

    # Extract text from PDF
    try:
        import pdfplumber
        import io
        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        text = "\n\n".join(parts)
    except Exception as e:
        text = f"[PDF uploaded — text extraction failed]\nError: {e}"

    save_user_resume(uid, text)
    return {"status": "ok"}


@app.post("/profile/auto-fill-from-resume")
async def auto_fill_profile(user: User) -> dict[str, Any]:
    uid = user["id"]
    resume_text = get_user_resume(uid)
    if not resume_text:
        raise HTTPException(status_code=404, detail="No resume text found. Upload a PDF first.")

    profile_data = extract_profile_from_resume(resume_text)

    # Save extracted profile
    prefs = get_user_preferences(uid)
    profile = prefs.get("profile_data", {})
    llm_p = profile_data.get("personal", profile_data)

    if "personal" not in profile:
        profile["personal"] = {}

    for k, v in llm_p.items():
        if v:
            profile["personal"][k] = v

    prefs["profile_data"] = profile
    update_user_preferences(uid, prefs)
    return {"status": "ok", "profile": profile}


# --- Settings ---

@app.get("/settings/preferences")
async def get_preferences(user: User) -> dict[str, Any]:
    uid = user["id"]
    return get_user_preferences(uid)


@app.put("/settings/preferences")
async def put_preferences(user: User, body: dict[str, Any]) -> dict[str, Any]:
    uid = user["id"]
    cur = get_user_preferences(uid)
    cur.update(body)
    update_user_preferences(uid, cur)
    return cur


class TelegramBody(BaseModel):
    chat_id: str | None = None


@app.post("/settings/telegram")
async def set_telegram(user: User, body: TelegramBody) -> dict[str, str]:
    update_user_telegram(user["id"], body.chat_id)
    return {"status": "ok"}


# --- LLM Features ---

class RecruiterDraftBody(BaseModel):
    job_id: str = Field(..., description="Job ID from the jobs list")
    recruiter_message: str = Field(..., min_length=1)
    language: str = "ru"
    tone: str = "professional_warm"


@app.post("/recruiter/draft-reply")
@limiter.limit("40/hour")
async def recruiter_draft_reply(request: Request, user: User, body: RecruiterDraftBody) -> dict[str, str]:
    uid = user["id"]
    url = try_decode_job_id(body.job_id)
    job = get_job_by_url(uid, url)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    text = generate_recruiter_reply_draft(
        dict(job),
        body.recruiter_message,
        language=body.language,
        tone=body.tone,
    )
    return {"draft": text, "note": "Draft only — you send it yourself (email/Telegram/other)."}


@app.post("/interview/prep/{job_id}")
@limiter.limit("30/hour")
async def interview_prep_ep(
    request: Request,
    user: User,
    job_id: str,
    language: str = Query("ru", description="ru or en"),
) -> dict[str, str]:
    uid = user["id"]
    url = try_decode_job_id(job_id)
    job = get_job_by_url(uid, url)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    md = generate_interview_prep(dict(job), language=language)
    return {"prep_markdown": md}


# --- Calendar ---

class CalendarEventBody(BaseModel):
    title: str = Field(..., min_length=1)
    starts_at: str = Field(..., description="ISO 8601, e.g. 2026-04-01T15:00:00+04:00")
    ends_at: str | None = None
    job_url: str | None = None
    notes: str | None = None


@app.post("/calendar/events")
async def calendar_create(user: User, body: CalendarEventBody) -> dict[str, Any]:
    eid = create_calendar_event(
        user["id"], body.title, body.starts_at, body.ends_at, body.job_url, body.notes,
    )
    return {"id": eid, "status": "ok"}


@app.get("/calendar/events")
async def calendar_list(user: User, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    return list_calendar_events(user["id"], limit=limit)


@app.delete("/calendar/events/{event_id}")
async def calendar_delete(user: User, event_id: int) -> dict[str, str]:
    if not delete_calendar_event(user["id"], event_id):
        raise HTTPException(404, "Event not found")
    return {"status": "ok"}


@app.get("/calendar/export.ics")
async def calendar_export_ics(user: User) -> Response:
    evs = list_calendar_events(user["id"], limit=500)
    body = events_to_ics(evs)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="armapply.ics"'},
    )


# --- Pipeline manual trigger ---

@app.post("/pipeline/trigger")
@limiter.limit("5/hour")
async def trigger_pipeline(request: Request, user: User) -> dict[str, str]:
    """Manually trigger the full pipeline for the authenticated user."""
    uid = user["id"]

    import threading
    from armapply.scheduler import run_pipeline_for_user

    def _run():
        try:
            run_pipeline_for_user(uid)
        except Exception as e:
            log.error("Manual pipeline failed for user %d: %s", uid, e)
            log_pipeline_run(uid, "manual_pipeline", "error", str(e)[:1000])

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "pipeline_started", "note": "Running in background. Check /monitoring/dashboard for results."}


PIPELINE_SECRET = os.environ.get("PIPELINE_SECRET", "")


@app.post("/pipeline/cron")
async def cron_trigger(request: Request) -> dict[str, Any]:
    """Trigger the daily pipeline for ALL active users.
    Called by GitHub Actions cron. Secured by PIPELINE_SECRET header.
    """
    secret = request.headers.get("X-Pipeline-Secret", "")
    if not PIPELINE_SECRET or secret != PIPELINE_SECRET:
        raise HTTPException(status_code=403, detail="Invalid or missing pipeline secret")

    import threading
    from armapply.scheduler import run_pipeline_all_users

    def _run():
        try:
            result = run_pipeline_all_users()
            log.info("Cron pipeline complete: %s", result)
        except Exception as e:
            log.error("Cron pipeline failed: %s", e)

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "cron_pipeline_started"}


# --- Telegram Webhook ---

@app.post("/telegram/webhook")
@app.post("/telegram/webhook/{user_id}")
async def telegram_webhook(request: Request, user_id: int | None = None) -> dict[str, str]:
    """Telegram bot webhook endpoint."""
    update = await request.json()
    effective_uid = user_id or 2

    try:
        from armapply.users_db import _exec
        from datetime import datetime, timezone
        _exec(
            "INSERT INTO api_logs (user_id, method, path, status_code, detail, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (effective_uid, "POST", f"/telegram/webhook/{user_id or ''}", 200, json.dumps(update), datetime.now(timezone.utc).isoformat())
        )
    except Exception as e:
        log.error("Failed to log webhook: %s", e)

    return {"ok": "true"}


# --- Health ---

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "armapply", "version": "2.0.0"}
