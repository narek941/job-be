"""ArmApply FastAPI application (AGPL-3.0 — extends ApplyPilot)."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from armapply.auth_deps import create_access_token, current_user, hash_password, verify_password
from armapply.config import DATA_ROOT
from armapply.job_codec import encode_job_id, try_decode_job_id
from armapply.pipeline_ops import (
    cover_letter_for_job,
    discover_and_enrich,
    run_auto_apply,
    save_uploaded_resume_pdf,
    save_uploaded_resume_text,
    score_jobs_batch,
    score_one_job,
    tailor_job_by_url,
)
from armapply.searches_build import write_searches_for_user
from armapply.telegram_notify import send_telegram_message
from armapply.tier_util import require_tier
from armapply.apply_policy import apply_limits_snapshot, assert_apply_allowed, fetch_smart_apply_queue
from armapply.calendar_ics import events_to_ics
from armapply.llm_features import generate_interview_prep, generate_recruiter_reply_draft, extract_profile_from_resume
from armapply.users_db import (
    create_calendar_event,
    create_user,
    delete_calendar_event,
    get_user_by_email,
    get_user_preferences,
    init_app_db,
    list_calendar_events,
    list_pipeline_runs,
    log_pipeline_run,
    update_user_preferences,
    update_user_telegram,
)
from armapply.dev_seed import maybe_seed_test_user
from armapply.workspace import activate_user_workspace, ensure_user_workspace, merge_profile_armapply, run_in_pipeline
from applypilot.pipeline import _run_enrich
from applypilot.discovery.telegram_channels import run_telegram_channel_discovery

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("armapply")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="ArmApply API", version="0.1.0", description="AGPL-3.0 — extends ApplyPilot")
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
    maybe_seed_test_user()
    
    # Start background scheduler
    import asyncio
    asyncio.create_task(background_pipeline_scheduler())

async def background_pipeline_scheduler():
    """Simple background loop to run discovery for all users periodically."""
    import asyncio
    await asyncio.sleep(10) # Wait for startup
    while True:
        log.info("Starting background pipeline sweep...")
        from armapply.users_db import list_all_user_ids
        try:
            uids = list_all_user_ids()
            for uid in uids:
                log.info(f"Background proc for user {uid}")
                try:
                    # 1. Discover & Enrich
                    def _disc():
                        return discover_and_enrich(uid, workers=1)
                    await run_in_pipeline(uid, _disc)
                    
                    # 2. Score
                    def _score():
                        return score_jobs_batch(uid, limit=20)
                    await run_in_pipeline(uid, _score)
                except Exception as e:
                    log.error(f"Background task failed for user {uid}: {e}")
            
            # Wait 4 hours between sweeps
            await asyncio.sleep(4 * 3600)
        except Exception as global_e:
            log.error(f"Global scheduler error: {global_e}")
            await asyncio.sleep(600)


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
    ensure_user_workspace(uid)
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


class SearchJobsBody(BaseModel):
    queries_en: list[str] = Field(default_factory=list)
    queries_hy: list[str] = Field(default_factory=list)
    locations: list[dict[str, Any]] | None = None
    linkedin: bool = True
    staff_am: bool = True
    indeed: bool = False
    country: str = "ARM"
    workers: int = 1


@app.post("/search-jobs")
@limiter.limit("10/hour")
async def search_jobs(request: Request, user: User, body: SearchJobsBody) -> dict[str, Any]:
    uid = user["id"]
    root = ensure_user_workspace(uid)
    write_searches_for_user(
        root,
        body.queries_en,
        body.queries_hy,
        body.locations,
        body.linkedin,
        body.staff_am,
        body.indeed,
        body.country,
    )

    def _go():
        require_tier(1, "Discovery")
        return discover_and_enrich(user_id=uid, workers=max(1, min(body.workers, 4)))

    result = await run_in_pipeline(uid, _go)
    log_pipeline_run(uid, "discover+enrich", "ok", json.dumps(result, default=str)[:2000])
    return {"status": "ok", "result": result}


class SearchTelegramBody(BaseModel):
    """Публичные каналы (username или @name). Посты с полным текстом попадают в БД как вакансии."""

    channels: list[str] = Field(..., min_length=1)
    max_pages_per_channel: int = Field(2, ge=1, le=10)
    keyword_filter: list[str] | None = Field(
        None,
        description="null = взять фильтр из searches.yaml; [] = все посты; непустой список = хотя бы одно вхождение.",
    )
    run_enrich: bool = False


@app.post("/search-jobs-telegram")
@limiter.limit("20/hour")
async def search_jobs_telegram(request: Request, user: User, body: SearchTelegramBody) -> dict[str, Any]:
    uid = user["id"]

    def _go():
        require_tier(1, "Discovery")
        tg = run_telegram_channel_discovery(
            channels=body.channels,
            max_pages_per_channel=body.max_pages_per_channel,
            keyword_filter=body.keyword_filter,
        )
        enrich = _run_enrich(workers=1) if body.run_enrich else {"status": "skipped"}
        return {"telegram": tg, "enrich": enrich}

    result = await run_in_pipeline(uid, _go)
    log_pipeline_run(uid, "telegram-discover", "ok", json.dumps(result, default=str)[:2000])
    return {"status": "ok", "result": result}


class ScoreJobsBody(BaseModel):
    limit: int = 0


@app.post("/score-jobs")
@limiter.limit("30/hour")
async def score_jobs_ep(request: Request, user: User, body: ScoreJobsBody) -> dict[str, Any]:
    uid = user["id"]

    def _go():
        require_tier(2, "AI scoring")
        return score_jobs_batch(user_id=uid, limit=body.limit)

    result = await run_in_pipeline(uid, _go)
    log_pipeline_run(uid, "score", "ok", json.dumps(result, default=str)[:2000])
    chat_id = user.get("telegram_chat_id")
    if chat_id and result.get("scored", 0) > 0:
        send_telegram_message(
            str(chat_id),
            f"ArmApply: scored {result.get('scored')} job(s). Open the app to review.",
        )
    return {"status": "ok", "result": result}


@app.post("/tailor-resume/{job_id}")
@limiter.limit("60/hour")
async def tailor_resume_ep(
    request: Request,
    user: User,
    job_id: str,
    min_score: int = 7,
    validation: str = "normal",
) -> dict[str, Any]:
    uid = user["id"]
    url = try_decode_job_id(job_id)

    def _go():
        require_tier(2, "Resume tailoring")
        return tailor_job_by_url(user_id=uid, url=url, min_score=min_score, validation_mode=validation)

    out = await run_in_pipeline(uid, _go)
    log_pipeline_run(uid, "tailor", "ok" if out.get("ok") else "fail", url[:500])
    return out


@app.post("/generate-cover-letter/{job_id}")
@limiter.limit("60/hour")
async def cover_letter_ep(
    request: Request,
    user: User,
    job_id: str,
    validation: str = "normal",
) -> dict[str, Any]:
    uid = user["id"]
    url = try_decode_job_id(job_id)

    def _go():
        require_tier(2, "Cover letters")
        return cover_letter_for_job(user_id=uid, url=url, validation_mode=validation)

    out = await run_in_pipeline(uid, _go)
    log_pipeline_run(uid, "cover", "ok" if out.get("ok") else "fail", url[:500])
    return out


@app.get("/jobs")
async def list_jobs(
    user: User,
    limit: int = Query(50, ge=1, le=200),
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    from armapply.users_db import get_jobs_for_user
    rows = get_jobs_for_user(user["id"], limit=limit, min_score=min_score)
    # Ensure every job gets a job_id for the frontend
    for r in rows:
        r["job_id"] = encode_job_id(r["url"])
    return rows


@app.get("/jobs/{job_id}")
async def get_job(user: User, job_id: str) -> dict[str, Any]:
    from armapply.users_db import get_job_by_url
    url = try_decode_job_id(job_id)
    job = get_job_by_url(user["id"], url)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job["job_id"] = job_id
    return job


class ApplyBody(BaseModel):
    dry_run: bool = True
    headless: bool = True
    min_score: int = 7
    model: str = "haiku"


@app.post("/apply-job/{job_id}")
@limiter.limit("20/hour")
async def apply_job_ep(request: Request, user: User, job_id: str, body: ApplyBody) -> dict[str, Any]:
    uid = user["id"]
    url = try_decode_job_id(job_id)
    prefs = get_user_preferences(uid)
    dry = bool(body.dry_run)
    if prefs.get("always_dry_run"):
        dry = True

    def _go():
        require_tier(3, "Auto-apply")
        if not dry:
            assert_apply_allowed(uid, prefs)
        run_auto_apply(
            job_url=url,
            min_score=body.min_score,
            dry_run=dry,
            headless=body.headless,
            model=body.model,
        )
        return {"submitted": not dry}

    try:
        await run_in_pipeline(uid, _go)
    except Exception as e:
        log.exception("apply failed")
        log_pipeline_run(uid, "apply", "error", str(e)[:1000])
        chat_id = user.get("telegram_chat_id")
        if chat_id:
            send_telegram_message(str(chat_id), f"ArmApply apply error: {e!s}"[:500])
        raise HTTPException(status_code=500, detail=str(e)) from e

    log_pipeline_run(uid, "apply", "ok", f"dry_run={dry} url={url[:200]}")
    chat_id = user.get("telegram_chat_id")
    if chat_id and not dry:
        send_telegram_message(str(chat_id), f"ArmApply: application flow finished for {url[:80]}…")
    return {"status": "ok", "dry_run": dry}


@app.get("/monitoring/dashboard")
async def monitoring_dashboard(user: User) -> dict[str, Any]:
    from armapply.users_db import get_jobs_stats_supabase
    uid = user["id"]
    stats = get_jobs_stats_supabase(uid)
    runs = list_pipeline_runs(uid, limit=100)
    return {
        "applypilot_stats": stats, 
        "daily_activity": [], 
        "pipeline_runs": runs
    }


@app.get("/profile")
async def get_profile(user: User) -> dict[str, Any]:
    uid = user["id"]
    root = ensure_user_workspace(uid)
    path = root / "profile.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@app.put("/profile")
async def update_profile(user: User, body: dict[str, Any]) -> dict[str, str]:
    uid = user["id"]
    root = ensure_user_workspace(uid)
    path = root / "profile.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    def deep_merge(a: dict, b: dict) -> dict:
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                deep_merge(a[k], v)
            else:
                a[k] = v
        return a

    deep_merge(data, body)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "ok"}


@app.post("/profile/resume")
async def upload_resume_text(user: User, text: str = Query(..., description="Plain-text resume")) -> dict[str, str]:
    uid = user["id"]
    root = ensure_user_workspace(uid)
    save_uploaded_resume_text(root, text)
    return {"status": "ok"}


@app.post("/profile/resume-pdf")
async def upload_resume_pdf(user: User, file: UploadFile = File(...)) -> dict[str, str]:
    uid = user["id"]
    root = ensure_user_workspace(uid)
    data = await file.read()
    save_uploaded_resume_pdf(root, data)
    return {"status": "ok"}


@app.post("/profile/auto-fill-from-resume")
async def auto_fill_profile(user: User) -> dict[str, Any]:
    uid = user["id"]
    root = ensure_user_workspace(uid)
    resume_path = root / "resume.txt"
    if not resume_path.exists():
        raise HTTPException(status_code=404, detail="No resume text found. Please upload a PDF first.")
    
    text = resume_path.read_text(encoding="utf-8")
    profile_data = extract_profile_from_resume(text)
    
    # Save the extracted profile
    data_path = root / "profile.json"
    current_data = {}
    if data_path.exists():
        current_data = json.loads(data_path.read_text(encoding="utf-8"))
    
    # Merge extracted data into personal section (handle both nested and flat results)
    llm_p = profile_data.get("personal", profile_data)
    
    if "personal" not in current_data:
        current_data["personal"] = {}
        
    for k, v in llm_p.items():
        if v: # Only update if we got a value
            current_data["personal"][k] = v
            
    data_path.write_text(json.dumps(current_data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "ok", "profile": current_data}


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
    lang_patch = {}
    if "resume_language" in body:
        lang_patch["resume_language"] = body["resume_language"]
    if "cover_letter_language" in body:
        lang_patch["cover_letter_language"] = body["cover_letter_language"]
    if lang_patch:
        merge_profile_armapply(uid, lang_patch)
    return cur


class TelegramBody(BaseModel):
    chat_id: str | None = None


@app.post("/settings/telegram")
async def set_telegram(user: User, body: TelegramBody) -> dict[str, str]:
    update_user_telegram(user["id"], body.chat_id)
    return {"status": "ok"}


@app.get("/files/resume.pdf")
async def download_resume_pdf(user: User):
    uid = user["id"]
    path = ensure_user_workspace(uid) / "resume.pdf"
    if not path.exists():
        raise HTTPException(404, "No PDF uploaded")
    return FileResponse(path, media_type="application/pdf", filename="resume.pdf")


@app.get("/apply/limits-status")
async def apply_limits_status(user: User) -> dict[str, Any]:
    uid = user["id"]
    prefs = get_user_preferences(uid)

    def _snap():
        activate_user_workspace(uid)
        return apply_limits_snapshot(uid, prefs)

    return await run_in_pipeline(uid, _snap)


@app.get("/jobs/apply-queue")
async def jobs_apply_queue(user: User, limit: int = Query(10, ge=1, le=50)) -> list[dict[str, Any]]:
    uid = user["id"]

    def _q():
        activate_user_workspace(uid)
        rows = fetch_smart_apply_queue(uid, limit=limit)
        return [_row_to_job(r) for r in rows]

    return await run_in_pipeline(uid, _q)


class RecruiterDraftBody(BaseModel):
    job_id: str = Field(..., description="Тот же id, что в списке вакансий")
    recruiter_message: str = Field(..., min_length=1)
    language: str = "ru"
    tone: str = "professional_warm"


@app.post("/recruiter/draft-reply")
@limiter.limit("40/hour")
async def recruiter_draft_reply(request: Request, user: User, body: RecruiterDraftBody) -> dict[str, str]:
    from armapply.users_db import get_job_by_url
    uid = user["id"]
    url = try_decode_job_id(body.job_id)
    job = get_job_by_url(uid, url)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    def _run():
        require_tier(2, "LLM drafts")
        # We still activate for path patching if needed, but query from Supabase
        activate_user_workspace(uid)
        return generate_recruiter_reply_draft(
            job,
            body.recruiter_message,
            language=body.language,
            tone=body.tone,
        )

    text = await run_in_pipeline(uid, _run)
    return {"draft": text, "note": "Только черновик — отправку делаете вы сами (почта/Telegram/hh)."}


@app.post("/interview/prep/{job_id}")
@limiter.limit("30/hour")
async def interview_prep_ep(
    request: Request,
    user: User,
    job_id: str,
    language: str = Query("ru", description="ru или en"),
) -> dict[str, str]:
    from armapply.users_db import get_job_by_url
    uid = user["id"]
    url = try_decode_job_id(job_id)
    job = get_job_by_url(uid, url)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    def _run():
        require_tier(2, "Interview prep")
        activate_user_workspace(uid)
        return generate_interview_prep(job, language=language)

    md = await run_in_pipeline(uid, _run)
    return {"prep_markdown": md}


class CalendarEventBody(BaseModel):
    title: str = Field(..., min_length=1)
    starts_at: str = Field(..., description="ISO 8601, например 2026-04-01T15:00:00+04:00")
    ends_at: str | None = None
    job_url: str | None = None
    notes: str | None = None


@app.post("/calendar/events")
async def calendar_create(user: User, body: CalendarEventBody) -> dict[str, Any]:
    eid = create_calendar_event(
        user["id"],
        body.title,
        body.starts_at,
        body.ends_at,
        body.job_url,
        body.notes,
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "armapply"}


