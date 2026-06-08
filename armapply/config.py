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
    worldwide_ratio_default: float
    min_score_notify_default: int
    min_score_auto_apply_default: int

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            database_url=_env_required("DATABASE_URL"),
            gemini_api_key=_env_required("GEMINI_API_KEY"),
            gemini_model=_env("GEMINI_MODEL", "gemini-2.5-flash"),
            telegram_bot_token=_env_required("TELEGRAM_BOT_TOKEN"),
            telegram_webhook_secret=_env("TELEGRAM_WEBHOOK_SECRET", ""),
            pipeline_secret=_env_required("PIPELINE_SECRET"),
            worldwide_ratio_default=_env_float("WORLDWIDE_RATIO_DEFAULT", 0.1),
            min_score_notify_default=_env_int("MIN_SCORE_NOTIFY_DEFAULT", 6),
            min_score_auto_apply_default=_env_int("MIN_SCORE_AUTO_APPLY_DEFAULT", 8),
        )


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings.load()
