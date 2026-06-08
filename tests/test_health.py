"""Smoke test for the FastAPI app. Patches settings + db so we don't need
real env vars or a database to run."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


def _fake_settings():
    from armapply.config import Settings
    return Settings(
        database_url="postgresql://u:p@h:5432/d",
        gemini_api_key="x",
        gemini_model="gemini-2.0-flash",
        telegram_bot_token="x",
        telegram_webhook_secret="",
        pipeline_secret="cron-secret",
        worldwide_ratio_default=0.1,
        min_score_notify_default=6,
        min_score_auto_apply_default=8,
    )


def test_health_endpoint() -> None:
    with patch("armapply.config.settings", return_value=_fake_settings()), \
         patch("armapply.db.run_migrations"):
        from armapply.main import app
        with TestClient(app) as client:
            r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_cron_requires_secret() -> None:
    with patch("armapply.config.settings", return_value=_fake_settings()), \
         patch("armapply.db.run_migrations"):
        from armapply.main import app
        with TestClient(app) as client:
            r = client.post("/cron")
    assert r.status_code == 403
