"""Tests for /gmail/* redirect routes (mobile → native Gmail app)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


def _fake_settings(app_url: str = "https://app.example.com") -> object:
    from armapply.config import Settings

    return Settings(
        database_url="postgresql://u:p@h:5432/d",
        gemini_api_key="x",
        gemini_model="gemini-2.0-flash",
        telegram_bot_token="x",
        telegram_webhook_secret="",
        pipeline_secret="cron-secret",
        gmail_address="",
        gmail_app_password="",
        google_client_id="",
        google_client_secret="",
        app_url=app_url,
        worldwide_ratio_default=0.1,
        min_score_notify_default=6,
        min_score_auto_apply_default=8,
    )


def test_gmail_compose_redirect_includes_app_scheme() -> None:
    with patch("armapply.config.settings", return_value=_fake_settings()), \
         patch("armapply.db.run_migrations"):
        from armapply.main import app

        with TestClient(app) as client:
            r = client.get(
                "/gmail/compose",
                params={"to": "hr@acme.com", "subject": "Hello", "body": "World"},
            )
    assert r.status_code == 200
    assert "googlegmail:///co?" in r.text
    assert "intent://co?" in r.text
    assert "hr%40acme.com" in r.text
    assert "mail.google.com" in r.text


def test_gmail_drafts_redirect_prefers_web_for_specific_draft() -> None:
    with patch("armapply.config.settings", return_value=_fake_settings()), \
         patch("armapply.db.run_migrations"):
        from armapply.main import app

        with TestClient(app) as client:
            r = client.get(
                "/gmail/drafts",
                params={"account": "me@gmail.com", "draft": "d999"},
            )
    assert r.status_code == 200
    assert "compose=d999" in r.text
    assert "preferWeb = true" in r.text
