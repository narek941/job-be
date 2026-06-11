"""JSON API for the web frontend (job-fe).

Auth is the Telegram Login Widget: the widget hands the browser a payload
signed by Telegram; we verify the HMAC exactly per
https://core.telegram.org/widgets/login#checking-authorization and mint a
session token. The token reuses the signed `{id}.{ts}.{hmac}` scheme from
gmail_api's OAuth state (HMAC-SHA256 with pipeline_secret, distinct
context prefix so the two token kinds can't be swapped), with a 30-day TTL
instead of 10 minutes. No JWT lib needed.

Telegram user ids equal private-chat ids, so a web login maps onto the
same `users.tg_chat_id` row the bot uses — one account either way.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, UploadFile

from jobfox import analytics, db
from jobfox.config import settings
from jobfox.discovery import DEFAULT_TELEGRAM_CHANNELS, _tg_username

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_SESSION_TTL_SECONDS = 30 * 24 * 3600
_LOGIN_MAX_AGE_SECONDS = 24 * 3600  # reject stale widget payloads (replay)


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    key = settings().pipeline_secret.encode()
    return hmac.new(key, f"websession:{payload}".encode(), hashlib.sha256).hexdigest()


def make_session_token(user_id: int) -> str:
    payload = f"{user_id}.{int(time.time())}"
    return f"{payload}.{_sign(payload)}"


def parse_session_token(token: str) -> int:
    """Returns the user_id. Raises ValueError on tamper/expiry/malformed."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    user_id_str, ts_str, sig = parts
    payload = f"{user_id_str}.{ts_str}"
    if not hmac.compare_digest(_sign(payload), sig):
        raise ValueError("bad signature")
    try:
        ts = int(ts_str)
        user_id = int(user_id_str)
    except ValueError as e:
        raise ValueError("bad numeric fields") from e
    if time.time() - ts > _SESSION_TTL_SECONDS:
        raise ValueError("expired")
    return user_id


def _current_user(authorization: str | None) -> db.User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        user_id = parse_session_token(authorization.removeprefix("Bearer ").strip())
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")
    user = db.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unknown user")
    return user


# ---------------------------------------------------------------------------
# Telegram Login Widget verification
# ---------------------------------------------------------------------------

def verify_telegram_login(payload: dict[str, Any]) -> int:
    """Validate a Login Widget payload, return the Telegram user id.

    Raises ValueError on any mismatch. The data-check-string is every
    received field except `hash`, sorted, joined with newlines; the key is
    SHA256(bot_token)."""
    data = {k: v for k, v in payload.items() if k != "hash"}
    their_hash = payload.get("hash")
    if not their_hash or "id" not in data:
        raise ValueError("missing hash or id")

    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hashlib.sha256(settings().telegram_bot_token.encode()).digest()
    our_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(our_hash, str(their_hash)):
        raise ValueError("bad hash")

    try:
        auth_date = int(data.get("auth_date", 0))
    except ValueError as e:
        raise ValueError("bad auth_date") from e
    if time.time() - auth_date > _LOGIN_MAX_AGE_SECONDS:
        raise ValueError("login payload too old")

    return int(data["id"])


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _public_user(user: db.User) -> dict[str, Any]:
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "queries": user["queries"],
        "locations": user["locations"],
        "telegram_channels": user["telegram_channels"],
        "default_channels": list(DEFAULT_TELEGRAM_CHANNELS),
        "muted_companies": user["muted_companies"],
        "auto_apply": user["auto_apply"],
        "min_score_notify": user["min_score_notify"],
        "min_score_auto_apply": user["min_score_auto_apply"],
        "worldwide_ratio": user["worldwide_ratio"],
        "paused": user["paused"],
        "desired_role": user["desired_role"],
        "salary_min": user["salary_min"],
        "salary_currency": user["salary_currency"],
        "employment_type": user["employment_type"],
        "portfolio_links": user["portfolio_links"],
        "tier": user["tier"],
        "applies_this_week": db.applies_this_week(user["id"]),
        "apply_quota": db.apply_quota(user["tier"]),
        "cv_uploaded": bool(user["cv_text"]),
        "cv_filename": user["cv_pdf_filename"],
        "gmail_connected": bool(user["gmail_refresh_token"]),
        "gmail_address": user["gmail_address"],
    }


def _public_job(job: db.Job) -> dict[str, Any]:
    return {
        "id": job["id"],
        "title": job["title"],
        "company": job["company"],
        "location": job["location"],
        "source": job["source"],
        "score": job["score"],
        "reason": job["reason"],
        "status": job["status"],
        "url": job["url"],
        "discovered_at": job["discovered_at"].isoformat(),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/auth/telegram")
def auth_telegram(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        tg_id = verify_telegram_login(payload)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"telegram auth failed: {e}")

    user = db.get_user_by_chat(tg_id)
    if user is None:
        user = db.create_user(tg_id)
        analytics.track(user["id"], "signup", {"surface": "web"})
    analytics.track(user["id"], "web_login")
    # The widget knows the display name; fill it in if the bot never did.
    first_name = str(payload.get("first_name") or "").strip()
    if first_name and not user["name"]:
        db.update_user(user["id"], name=first_name)

    return {"token": make_session_token(user["id"]), "user": _public_user(user)}


@router.get("/me")
def get_me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    return _public_user(_current_user(authorization))


# Field name → validator/normalizer. Anything not listed here is rejected,
# so the web surface can never touch tokens, CV bytes, etc.
def _norm_str_list(v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError("expected a list of strings")
    return [x.strip() for x in v if x.strip()]


def _norm_score(v: Any) -> int:
    iv = int(v)
    if not 1 <= iv <= 10:
        raise ValueError("score must be 1-10")
    return iv


def _norm_salary_min(v: Any) -> int | None:
    if v is None or v == "":
        return None
    iv = int(v)
    if iv < 0:
        raise ValueError("salary must be >= 0")
    return iv


def _norm_employment_type(v: Any) -> str:
    sv = str(v).strip().lower()
    if sv not in {"any", "full_time", "part_time", "contract"}:
        raise ValueError("expected any|full_time|part_time|contract")
    return sv


def _norm_portfolio(v: Any) -> list[str]:
    links = _norm_str_list(v)
    for link in links:
        if not link.startswith(("http://", "https://")):
            raise ValueError(f"links must start with http(s):// — got {link[:60]}")
    return links[:10]


# NOTE: `tier` and `stripe_customer_id` are deliberately absent — billing
# state is written only by the Stripe webhook / ops.
_PATCHABLE: dict[str, Any] = {
    "name": lambda v: str(v).strip()[:200],
    "email": lambda v: str(v).strip()[:320],
    "queries": _norm_str_list,
    "locations": _norm_str_list,
    "muted_companies": _norm_str_list,
    "auto_apply": bool,
    "paused": bool,
    "min_score_notify": _norm_score,
    "min_score_auto_apply": _norm_score,
    "worldwide_ratio": lambda v: min(1.0, max(0.0, float(v))),
    "desired_role": lambda v: str(v).strip()[:200] or None,
    "salary_min": _norm_salary_min,
    "salary_currency": lambda v: str(v).strip().upper()[:8] or "USD",
    "employment_type": _norm_employment_type,
    "portfolio_links": _norm_portfolio,
}


def _norm_channels(v: Any) -> list[str]:
    """Extra channels only — defaults are fixed and always scanned."""
    defaults = {_tg_username(c).lower() for c in DEFAULT_TELEGRAM_CHANNELS}
    return [
        _tg_username(c)
        for c in _norm_str_list(v)
        if _tg_username(c) and _tg_username(c).lower() not in defaults
    ]


_PATCHABLE["telegram_channels"] = _norm_channels


@router.patch("/me")
def patch_me(
    changes: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    user = _current_user(authorization)
    bad = set(changes) - set(_PATCHABLE)
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown fields: {sorted(bad)}")
    normalized: dict[str, Any] = {}
    for key, value in changes.items():
        try:
            normalized[key] = _PATCHABLE[key](value)
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=f"bad value for {key}: {e}")
    if normalized:
        db.update_user(user["id"], **normalized)
    fresh = db.get_user(user["id"])
    assert fresh is not None
    return _public_user(fresh)


@router.post("/me/cv")
async def upload_cv(
    file: UploadFile,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """PDF CV upload — same pipeline as the Telegram path: extract text,
    store raw artifacts first, then best-effort structured profile."""
    from jobfox import match, profile as profile_mod

    user = _current_user(authorization)
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="upload a PDF")
    pdf_bytes = await file.read()
    if len(pdf_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="PDF too large (max 10 MB)")

    text = match.extract_cv_text(pdf_bytes)
    if len(text.strip()) < 100:
        raise HTTPException(
            status_code=422,
            detail="couldn't extract enough text from that PDF — try a different export",
        )

    db.update_user(
        user["id"],
        cv_text=text,
        cv_pdf=pdf_bytes,
        cv_pdf_filename=(file.filename or "cv.pdf")[:200],
    )
    analytics.track(user["id"], "cv_uploaded", {"surface": "web"})

    profile_ok = True
    try:
        parsed = profile_mod.extract_profile(text)
        db.update_user(user["id"], cv_profile=dict(parsed))
    except Exception:
        log.exception("web cv profile extraction failed user=%d", user["id"])
        profile_ok = False

    fresh = db.get_user(user["id"])
    assert fresh is not None
    return {"user": _public_user(fresh), "profile_parsed": profile_ok}


@router.get("/me/export")
def export_me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """GDPR-style export: every row we hold about the user, secrets excluded."""
    user = _current_user(authorization)
    return db.export_user_data(user["id"])


@router.delete("/me")
def delete_me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Hard delete + Gmail grant revocation. No soft-delete, no grace period —
    the Privacy Policy promises immediate removal."""
    from jobfox import gmail_api

    user = _current_user(authorization)
    revoked = gmail_api.revoke_token(user.get("gmail_refresh_token"))
    db.delete_user(user["id"])
    log.info("user %d deleted (gmail revoked=%s)", user["id"], revoked)
    return {"deleted": True, "gmail_revoked": revoked}


@router.get("/stats")
def get_stats(
    days: int = 30,
    authorization: str | None = Header(default=None),
) -> dict[str, int]:
    user = _current_user(authorization)
    return db.funnel_stats(user["id"], days=max(1, min(365, days)))


@router.get("/jobs")
def get_jobs(
    limit: int = 50,
    authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    user = _current_user(authorization)
    jobs = db.list_recent_jobs(user["id"], limit=max(1, min(200, limit)))
    return [_public_job(j) for j in jobs]


@router.get("/jobs/{job_id}/events")
def get_job_events(
    job_id: int,
    authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    """Per-job timeline — applied → replied → interview/offer/rejected."""
    user = _current_user(authorization)
    return db.list_job_events(job_id, user["id"])
