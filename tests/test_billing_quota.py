import hashlib
import hmac
import time

import pytest

from jobfox import billing, crypto, db
from jobfox.apply import QuotaExceeded
from jobfox.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        database_url="postgres://x",
        gemini_api_key="x",
        gemini_model="m",
        telegram_bot_token="t",
        telegram_webhook_secret="",
        pipeline_secret="s",
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
        stripe_secret_key="sk_test_x",
        stripe_webhook_secret="whsec_test",
        stripe_price_pro="price_pro",
        stripe_price_power="price_power",
        token_encryption_key="",
    )
    base.update(overrides)
    return Settings(**base)


def test_tier_quotas() -> None:
    assert db.apply_quota("free") == 5
    assert db.apply_quota("pro") == 50
    assert db.apply_quota("power") == 200
    assert db.apply_quota("unknown-tier") == 5  # safe default


def test_apply_quota_unlimited_tier_has_no_cap() -> None:
    assert db.apply_quota("unlimited") is None


def test_quota_exceeded_message() -> None:
    q = QuotaExceeded(tier="free", used=5, limit=5)
    assert "5/5" in str(q) and "free" in str(q)


def test_stripe_signature_roundtrip() -> None:
    secret = "whsec_test"
    payload = b'{"type":"checkout.session.completed"}'
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    assert billing.verify_stripe_signature(payload, header, secret)
    assert not billing.verify_stripe_signature(payload, header, "whsec_other")
    assert not billing.verify_stripe_signature(b"tampered", header, secret)


def test_stripe_signature_rejects_stale() -> None:
    secret = "whsec_test"
    payload = b"{}"
    ts = str(int(time.time()) - 3600)
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    assert not billing.verify_stripe_signature(payload, f"t={ts},v1={sig}", secret)


def test_plan_for_price(monkeypatch) -> None:
    monkeypatch.setattr(billing.config, "settings", lambda: _settings())
    assert billing._plan_for_price("price_pro") == "pro"
    assert billing._plan_for_price("price_power") == "power"
    assert billing._plan_for_price("price_other") is None


def test_token_encryption_roundtrip(monkeypatch) -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto.config, "settings", lambda: _settings(token_encryption_key=key))
    ct = crypto.encrypt_token("refresh-token-123")
    assert ct is not None and ct.startswith("enc:") and "refresh-token-123" not in ct
    assert crypto.decrypt_token(ct) == "refresh-token-123"
    # Legacy plaintext passes through untouched.
    assert crypto.decrypt_token("plain-old-token") == "plain-old-token"


def test_token_encryption_plaintext_mode(monkeypatch) -> None:
    monkeypatch.setattr(crypto.config, "settings", lambda: _settings(token_encryption_key=""))
    assert crypto.encrypt_token("tok") == "tok"
    with pytest.raises(RuntimeError):
        crypto.decrypt_token("enc:something")  # key lost must be loud
