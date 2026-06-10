"""Typed runtime configuration. All values come from environment variables.

Import `settings` once and treat it as immutable. `Settings.load()` is called
exactly once at startup; tests can build their own instance and inject it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_required(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Required environment variable {key!r} is not set")
    return val


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw else default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return float(raw) if raw else default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    gemini_api_key: str
    gemini_model: str
    telegram_bot_token: str
    telegram_webhook_secret: str
    pipeline_secret: str
    # Optional — only required when auto-apply is enabled for any user.
    gmail_address: str
    gmail_app_password: str
    # Per-user Gmail OAuth (drafts API). All three must be set for
    # /connect_gmail to work; if any is empty the apply flow stays on
    # SMTP / deep_link. app_url is the public origin used for the
    # OAuth redirect URI (must EXACTLY match what's whitelisted in the
    # Google Cloud Console — including scheme and trailing slash policy).
    google_client_id: str
    google_client_secret: str
    app_url: str
    worldwide_ratio_default: float
    min_score_notify_default: int
    min_score_auto_apply_default: int

    @property
    def smtp_configured(self) -> bool:
        return bool(self.gmail_address and self.gmail_app_password)

    @property
    def gmail_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.app_url)

    @property
    def gmail_redirect_uri(self) -> str:
        return self.app_url.rstrip("/") + "/oauth/google/callback"

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            database_url=_env_required("DATABASE_URL"),
            gemini_api_key=_env_required("GEMINI_API_KEY"),
            gemini_model=_env("GEMINI_MODEL", "gemini-2.5-flash"),
            telegram_bot_token=_env_required("TELEGRAM_BOT_TOKEN"),
            telegram_webhook_secret=_env("TELEGRAM_WEBHOOK_SECRET", ""),
            pipeline_secret=_env_required("PIPELINE_SECRET"),
            gmail_address=_env("GMAIL_ADDRESS", ""),
            gmail_app_password=_env("GMAIL_APP_PASSWORD", ""),
            google_client_id=_env("GOOGLE_CLIENT_ID", ""),
            google_client_secret=_env("GOOGLE_CLIENT_SECRET", ""),
            app_url=_env("APP_URL", ""),
            worldwide_ratio_default=_env_float("WORLDWIDE_RATIO_DEFAULT", 0.1),
            min_score_notify_default=_env_int("MIN_SCORE_NOTIFY_DEFAULT", 6),
            min_score_auto_apply_default=_env_int("MIN_SCORE_AUTO_APPLY_DEFAULT", 8),
        )


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings.load()
