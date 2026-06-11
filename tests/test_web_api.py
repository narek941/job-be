import hashlib
import hmac
import time

import pytest

from jobfox import web_api
from jobfox.config import Settings


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    s = Settings(
        database_url="postgres://x",
        gemini_api_key="x",
        gemini_model="m",
        telegram_bot_token="123456:TEST_BOT_TOKEN",
        telegram_webhook_secret="",
        pipeline_secret="test-pipeline-secret",
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
    monkeypatch.setattr(web_api, "settings", lambda: s)
    return s


def test_session_token_roundtrip() -> None:
    token = web_api.make_session_token(42)
    assert web_api.parse_session_token(token) == 42


def test_session_token_tamper_rejected() -> None:
    token = web_api.make_session_token(42)
    user_id, ts, sig = token.split(".")
    with pytest.raises(ValueError):
        web_api.parse_session_token(f"43.{ts}.{sig}")


def test_session_token_not_interchangeable_with_oauth_state(
    fake_settings, monkeypatch
) -> None:
    # Same {id}.{ts}.{hmac} shape as gmail_api.make_state, but the context
    # prefix must make the signatures differ.
    from jobfox import gmail_api

    monkeypatch.setattr(gmail_api, "settings", lambda: fake_settings)
    payload = "42.1000000000"
    assert web_api._sign(payload) != gmail_api._sign(payload)


def _signed_login_payload(bot_token: str, tg_id: int, **extra) -> dict:
    data = {"id": tg_id, "auth_date": int(time.time()), **extra}
    check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(bot_token.encode()).digest()
    data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return data


def test_verify_telegram_login_accepts_valid(fake_settings) -> None:
    payload = _signed_login_payload(
        fake_settings.telegram_bot_token, 777, first_name="Narek", username="narek"
    )
    assert web_api.verify_telegram_login(payload) == 777


def test_verify_telegram_login_rejects_forged_hash(fake_settings) -> None:
    payload = _signed_login_payload(fake_settings.telegram_bot_token, 777)
    payload["hash"] = "0" * 64
    with pytest.raises(ValueError):
        web_api.verify_telegram_login(payload)


def test_verify_telegram_login_rejects_tampered_id(fake_settings) -> None:
    payload = _signed_login_payload(fake_settings.telegram_bot_token, 777)
    payload["id"] = 778
    with pytest.raises(ValueError):
        web_api.verify_telegram_login(payload)


def test_verify_telegram_login_rejects_stale(fake_settings) -> None:
    payload_data = {"id": 777, "auth_date": int(time.time()) - 3 * 24 * 3600}
    check_string = "\n".join(f"{k}={payload_data[k]}" for k in sorted(payload_data))
    secret = hashlib.sha256(fake_settings.telegram_bot_token.encode()).digest()
    payload_data["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(ValueError):
        web_api.verify_telegram_login(payload_data)


def test_delete_me_revokes_and_deletes(fake_settings, monkeypatch) -> None:
    from jobfox import db, gmail_api

    calls: dict[str, object] = {}
    fake_user = {"id": 42, "gmail_refresh_token": "tok-123"}
    monkeypatch.setattr(db, "get_user", lambda uid: fake_user if uid == 42 else None)
    monkeypatch.setattr(db, "delete_user", lambda uid: calls.setdefault("deleted", uid))
    def fake_revoke(t):
        calls["revoked"] = t
        return True

    monkeypatch.setattr(gmail_api, "revoke_token", fake_revoke)

    token = web_api.make_session_token(42)
    out = web_api.delete_me(authorization=f"Bearer {token}")
    assert out == {"deleted": True, "gmail_revoked": True}
    assert calls == {"revoked": "tok-123", "deleted": 42}


def test_export_excludes_secrets() -> None:
    from jobfox import db

    assert "gmail_refresh_token" in db._EXPORT_EXCLUDED_USER_FIELDS
    assert "cv_pdf" in db._EXPORT_EXCLUDED_USER_FIELDS


def test_patch_normalizers() -> None:
    assert web_api._PATCHABLE["worldwide_ratio"](2.5) == 1.0
    assert web_api._PATCHABLE["worldwide_ratio"](-1) == 0.0
    assert web_api._PATCHABLE["queries"](["  python ", "", "react"]) == ["python", "react"]
    with pytest.raises(ValueError):
        web_api._PATCHABLE["min_score_notify"](11)
    # Defaults can't be smuggled in as extra channels.
    assert web_api._PATCHABLE["telegram_channels"](["@STAFFAM", "my_chan"]) == ["my_chan"]


def test_patch_profile_v2_normalizers() -> None:
    assert web_api._PATCHABLE["salary_min"](None) is None
    assert web_api._PATCHABLE["salary_min"]("3000") == 3000
    with pytest.raises(ValueError):
        web_api._PATCHABLE["salary_min"](-5)
    assert web_api._PATCHABLE["salary_currency"]("usd") == "USD"
    assert web_api._PATCHABLE["employment_type"]("Full_Time") == "full_time"
    with pytest.raises(ValueError):
        web_api._PATCHABLE["employment_type"]("gig")
    assert web_api._PATCHABLE["portfolio_links"](["https://github.com/x"]) == [
        "https://github.com/x"
    ]
    with pytest.raises(ValueError):
        web_api._PATCHABLE["portfolio_links"](["github.com/x"])
    # Billing state must not be user-writable.
    assert "tier" not in web_api._PATCHABLE
    assert "stripe_customer_id" not in web_api._PATCHABLE
