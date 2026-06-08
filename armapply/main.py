"""FastAPI entry point.

Only three HTTP routes:
  GET  /health             liveness
  POST /telegram/webhook   Telegram pushes updates here
  POST /cron               daily orchestrator (secured by a shared secret header)

Everything user-facing happens via the Telegram bot in `bot.py`.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from armapply import bot, db, pipeline
from armapply.config import settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Validate config and run migrations up front so a misconfigured deploy
    # fails fast instead of at first request.
    _ = settings()
    db.run_migrations()
    log.info("startup complete")
    yield


app = FastAPI(title="ArmApply", version="2.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    try:
        bot.handle_update(update)
    except Exception:
        log.exception("update handler failed: %s", update)
    return {"ok": "true"}


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

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}
