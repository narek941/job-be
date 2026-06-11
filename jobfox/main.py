"""FastAPI entry point.

Only five HTTP routes:
  GET  /health             liveness
  GET  /gmail/compose      mobile-friendly redirect → Gmail app compose
  GET  /gmail/drafts       mobile-friendly redirect → Gmail app drafts
  POST /telegram/webhook   Telegram pushes updates here
  POST /cron               daily orchestrator (secured by a shared secret header)

Everything user-facing happens via the Telegram bot in `bot.py`.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from jobfox import (
    analytics,
    billing,
    bot,
    branding,
    db,
    gmail_api,
    pipeline,
    reply_tracking,
    telegram_api,
    web_api,
)
from jobfox.config import settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Validate config and run migrations up front so a misconfigured deploy
    # fails fast instead of at first request.
    _ = settings()
    analytics.init_sentry()
    db.run_migrations()
    log.info("startup complete")
    yield


app = FastAPI(title="JobFox", version="2.0.0", lifespan=lifespan)
app.include_router(web_api.router)
app.include_router(billing.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/logo.svg", include_in_schema=False)
def logo() -> FileResponse:
    return FileResponse(_STATIC_DIR / "logo.svg", media_type="image/svg+xml")


def _verify_telegram_secret(header_value: str | None) -> None:
    expected = settings().telegram_webhook_secret
    if not expected:
        return  # no secret configured → skip check (dev mode)
    if header_value != expected:
        raise HTTPException(status_code=403, detail="invalid webhook secret")


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, str]:
    _verify_telegram_secret(x_telegram_bot_api_secret_token)
    update: dict[str, Any] = await request.json()

    # Ack Telegram immediately — anything slow (CV extraction, /run) happens
    # in a worker thread so the webhook never times out and Telegram never
    # retries (which would otherwise re-trigger the same OOM-prone work).
    def _process() -> None:
        try:
            bot.handle_update(update)
        except Exception:
            log.exception("update handler failed: %s", update)

    threading.Thread(target=_process, daemon=True).start()
    return {"ok": "true"}


_LOGO_HEADER = branding.logo_mark_svg(44)

_OAUTH_OK_HTML = (
    """<!doctype html><meta charset="utf-8">
<title>Gmail connected — JobFox</title>
<link rel="icon" href="/logo.svg" type="image/svg+xml">
<style>body{{font:16px system-ui;margin:48px auto;max-width:480px;color:#222}}
.ok{{color:#0a7d28}} .hint{{color:#666;font-size:14px}}</style>
"""
    + _LOGO_HEADER
    + """
<h2 class="ok">✅ Gmail connected</h2>
<p>You're hooked up as <code>{email}</code>.</p>
<p class="hint">Head back to Telegram. Future "Apply" taps will create a
real Gmail draft (with your CV attached) in your account, ready to review
and send.</p>"""
)

_OAUTH_ERR_HTML = (
    """<!doctype html><meta charset="utf-8">
<title>Gmail connect failed — JobFox</title>
<link rel="icon" href="/logo.svg" type="image/svg+xml">
<style>body{{font:16px system-ui;margin:48px auto;max-width:480px;color:#222}}
.err{{color:#b00020}} pre{{background:#f6f6f6;padding:12px;border-radius:6px;
white-space:pre-wrap;font-size:13px}}</style>
"""
    + _LOGO_HEADER
    + """
<h2 class="err">⚠️ Gmail connection failed</h2>
<pre>{reason}</pre>
<p>Re-run <code>/connect_gmail</code> in Telegram to get a fresh link.</p>"""
)


@app.get("/oauth/google/callback", response_class=HTMLResponse)
def oauth_google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """Google redirects the user's browser here after they grant /
    deny the consent screen kicked off by /connect_gmail in Telegram.

    We validate the signed `state` (binds the callback back to the chat
    that initiated it — no CSRF), exchange the code for a refresh token,
    persist it, then ping the user in Telegram so they don't have to
    figure out "did it work?" from the browser tab alone.
    """
    if error:
        return HTMLResponse(_OAUTH_ERR_HTML.format(reason=f"Google: {error}"), status_code=400)
    if not code or not state:
        return HTMLResponse(
            _OAUTH_ERR_HTML.format(reason="Missing ?code or ?state from Google."),
            status_code=400,
        )
    try:
        chat_id = gmail_api.parse_state(state)
    except ValueError as e:
        return HTMLResponse(
            _OAUTH_ERR_HTML.format(reason=f"Invalid/expired state: {e}"),
            status_code=400,
        )

    user = db.get_user_by_chat(chat_id)
    if user is None:
        return HTMLResponse(
            _OAUTH_ERR_HTML.format(reason="Unknown chat — /start the bot first."),
            status_code=400,
        )

    try:
        refresh_token, gmail_address = gmail_api.exchange_code(code)
    except Exception as e:
        log.exception("Gmail OAuth code exchange failed for chat=%s", chat_id)
        # Tell the user in Telegram too — they won't necessarily look at
        # the browser tab again.
        try:
            telegram_api.send_message(
                chat_id, f"⚠️ Gmail connect failed: {e}"
            )
        except Exception:
            pass
        return HTMLResponse(_OAUTH_ERR_HTML.format(reason=str(e)), status_code=500)

    db.update_user(
        user["id"],
        gmail_refresh_token=refresh_token,
        gmail_address=gmail_address,
    )
    try:
        telegram_api.send_message(
            chat_id,
            f"✅ Gmail connected as {gmail_address}. "
            "Future *Apply* taps will drop a draft in your Gmail with the CV attached.",
            parse_mode="Markdown",
        )
    except Exception:
        log.exception("post-OAuth Telegram nudge failed")

    return HTMLResponse(_OAUTH_OK_HTML.format(email=gmail_address))


@app.get("/gmail/compose", response_class=HTMLResponse)
def gmail_compose_redirect(
    to: str = "",
    subject: str = "",
    body: str = "",
) -> HTMLResponse:
    """Open a pre-filled Gmail compose screen — native app on mobile."""
    web = gmail_api.web_compose_url(to=to or None, subject=subject, body=body)
    ios = gmail_api.app_compose_url(to=to or None, subject=subject, body=body)
    android = gmail_api.app_compose_intent(
        to=to or None, subject=subject, body=body, fallback_web=web,
    )
    return HTMLResponse(gmail_api.gmail_redirect_html(web, ios_url=ios, android_url=android))


@app.get("/gmail/drafts", response_class=HTMLResponse)
def gmail_drafts_redirect(account: str = "", draft: str = "") -> HTMLResponse:
    """Open Gmail drafts — specific draft when ?draft= is set."""
    acct = account or None
    draft_id = draft or None
    web = gmail_api.drafts_url(acct, draft_id=draft_id)
    ios = gmail_api.app_gmail_url()
    android = gmail_api.app_gmail_intent(fallback_web=web)
    # A draft id only resolves via the web URL; the native app has no
    # supported deep link to a specific draft.
    return HTMLResponse(
        gmail_api.gmail_redirect_html(
            web, ios_url=ios, android_url=android, prefer_web=bool(draft_id),
        )
    )


@app.post("/cron")
def cron(x_pipeline_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """Daily pipeline trigger. Runs in a background thread so the HTTP call
    returns immediately — GitHub Actions doesn't need to hold the connection
    open for minutes."""
    expected = settings().pipeline_secret
    if not expected or x_pipeline_secret != expected:
        raise HTTPException(status_code=403, detail="invalid pipeline secret")

    def _run() -> None:
        try:
            pipeline.run_all()
        except Exception:
            log.exception("daily pipeline failed")
        # Opportunistic reply check right after discovery/apply work.
        try:
            reply_tracking.run_all()
        except Exception:
            log.exception("post-pipeline reply poll failed")

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.post("/cron/replies")
def cron_replies(x_pipeline_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """Hourly reply-tracking trigger (same shared secret as /cron)."""
    expected = settings().pipeline_secret
    if not expected or x_pipeline_secret != expected:
        raise HTTPException(status_code=403, detail="invalid pipeline secret")

    def _run() -> None:
        try:
            reply_tracking.run_all()
        except Exception:
            log.exception("reply poll failed")

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}
