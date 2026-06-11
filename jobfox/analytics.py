"""Product analytics (PostHog) and error tracking (Sentry) — both optional.

`track()` is fire-and-forget on a daemon thread: a PostHog outage or a
missing key must never slow down or break a user-facing path. Events use
`user_<id>` as distinct_id — never email or telegram username, so the
analytics store holds no direct PII.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import httpx

# Module (not name) import: callers and tests patch `jobfox.config.settings`;
# resolving the attribute at call time keeps those patches effective here.
from jobfox import config

log = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


def init_sentry() -> None:
    """Call once at startup. No-op without SENTRY_DSN."""
    dsn = config.settings().sentry_dsn
    if not dsn:
        return
    try:
        import sentry_sdk  # type: ignore[import-not-found]

        sentry_sdk.init(dsn=dsn, traces_sample_rate=0.1)
        log.info("Sentry initialized")
    except ImportError:
        log.warning("SENTRY_DSN set but sentry-sdk not installed — skipping")


def track(user_id: int, event: str, properties: dict[str, Any] | None = None) -> None:
    """Send one PostHog event. Never raises, never blocks the caller."""
    s = config.settings()
    if not s.posthog_api_key:
        return

    payload = {
        "api_key": s.posthog_api_key,
        "event": event,
        "distinct_id": f"user_{user_id}",
        "properties": properties or {},
    }

    def _send() -> None:
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                client.post(f"{s.posthog_host.rstrip('/')}/capture/", json=payload)
        except Exception:
            log.debug("posthog capture failed for %s", event, exc_info=True)

    threading.Thread(target=_send, daemon=True).start()
