"""HMAC-signed OAuth state token round-trip + tamper / expiry checks.

The state token is what binds Google's /oauth/google/callback hit back to
the Telegram chat that initiated /connect_gmail. If signing is wrong, a
stranger could connect THEIR Gmail account to YOUR chat — so this is the
one part of the Gmail integration that absolutely needs unit coverage.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


def _fake_settings(secret: str = "test-pipeline-secret"):
    from jobfox.config import Settings
    return Settings(
        database_url="postgresql://u:p@h:5432/d",
        gemini_api_key="x",
        gemini_model="gemini-2.0-flash",
        telegram_bot_token="x",
        telegram_webhook_secret="",
        pipeline_secret=secret,
        gmail_address="",
        gmail_app_password="",
        google_client_id="",
        google_client_secret="",
        app_url="",
        worldwide_ratio_default=0.1,
        min_score_notify_default=6,
        min_score_auto_apply_default=8,
        sentry_dsn="",
        posthog_api_key="",
        posthog_host="",
    )


def test_state_roundtrip() -> None:
    from jobfox import gmail_api

    with patch("jobfox.gmail_api.settings", return_value=_fake_settings()):
        token = gmail_api.make_state(12345)
        assert gmail_api.parse_state(token) == 12345


def test_state_rejects_tampered_payload() -> None:
    """Flipping the chat_id in a valid token must invalidate the HMAC."""
    from jobfox import gmail_api

    with patch("jobfox.gmail_api.settings", return_value=_fake_settings()):
        token = gmail_api.make_state(12345)
        # Swap chat_id 12345 → 99999, keep original signature
        chat_id, ts, sig = token.split(".")
        forged = f"99999.{ts}.{sig}"
        with pytest.raises(ValueError, match="bad signature"):
            gmail_api.parse_state(forged)


def test_state_rejects_wrong_secret() -> None:
    """A token signed by a different pipeline_secret must not parse."""
    from jobfox import gmail_api

    with patch("jobfox.gmail_api.settings", return_value=_fake_settings("secret-A")):
        token = gmail_api.make_state(12345)
    with patch("jobfox.gmail_api.settings", return_value=_fake_settings("secret-B")):
        with pytest.raises(ValueError, match="bad signature"):
            gmail_api.parse_state(token)


def test_state_rejects_expired_token() -> None:
    from jobfox import gmail_api

    with patch("jobfox.gmail_api.settings", return_value=_fake_settings()):
        old_ts = int(time.time()) - 11 * 60  # 11 min old; TTL is 10 min
        payload = f"42.{old_ts}"
        sig = gmail_api._sign(payload)
        token = f"{payload}.{sig}"
        with pytest.raises(ValueError, match="expired"):
            gmail_api.parse_state(token)


def test_state_rejects_malformed_token() -> None:
    from jobfox import gmail_api

    with patch("jobfox.gmail_api.settings", return_value=_fake_settings()):
        with pytest.raises(ValueError, match="malformed"):
            gmail_api.parse_state("not-a-real-token")


def test_drafts_url_picks_account() -> None:
    from jobfox import gmail_api

    assert (
        gmail_api.drafts_url("alice@gmail.com")
        == "https://mail.google.com/mail/u/alice@gmail.com/#drafts"
    )
    assert gmail_api.drafts_url(None) == "https://mail.google.com/mail/u/0/#drafts"
    assert gmail_api.drafts_url("") == "https://mail.google.com/mail/u/0/#drafts"
    assert (
        gmail_api.drafts_url("alice@gmail.com", draft_id="d123")
        == "https://mail.google.com/mail/u/alice@gmail.com/#drafts?compose=d123"
    )


def test_gmail_link_url_uses_app_redirect() -> None:
    from jobfox import gmail_api
    from jobfox.config import Settings

    s = Settings(
        database_url="postgresql://u:p@h:5432/d",
        gemini_api_key="x",
        gemini_model="gemini-2.0-flash",
        telegram_bot_token="x",
        telegram_webhook_secret="",
        pipeline_secret="test-pipeline-secret",
        gmail_address="",
        gmail_app_password="",
        google_client_id="",
        google_client_secret="",
        app_url="https://app.example.com",
        worldwide_ratio_default=0.1,
        min_score_notify_default=6,
        min_score_auto_apply_default=8,
        sentry_dsn="",
        posthog_api_key="",
        posthog_host="",
    )
    with patch("jobfox.gmail_api.settings", return_value=s):
        url = gmail_api.gmail_link_url(
            kind="compose", to="hr@acme.com", subject="Hi", body="Hello",
        )
    assert url.startswith("https://app.example.com/gmail/compose?")
    assert "to=hr%40acme.com" in url
    assert "subject=Hi" in url
    assert "body=Hello" in url


def test_gmail_link_url_falls_back_without_app_url() -> None:
    from jobfox import gmail_api

    with patch("jobfox.gmail_api.settings", return_value=_fake_settings()):
        url = gmail_api.gmail_link_url(kind="drafts", gmail_address="a@gmail.com", draft_id="x1")
    assert url == "https://mail.google.com/mail/u/a@gmail.com/#drafts?compose=x1"


def test_app_compose_url() -> None:
    from jobfox import gmail_api

    url = gmail_api.app_compose_url(to="a@b.com", subject="S", body="B")
    assert url.startswith("googlegmail:///co?")
    assert "to=a%40b.com" in url
    assert "subject=S" in url
    assert "body=B" in url
