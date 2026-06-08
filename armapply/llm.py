"""Gemini client (native API, not the OpenAI-compatible shim).

Two entry points:
  * `complete()`        — plain-text reply
  * `complete_json()`   — uses Gemini's native JSON mode

The native API is more reliable than the OpenAI compatibility layer (which
404s for some key+model combinations) and gives us strict JSON output for
free, so no regex fallback is needed.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from armapply.config import settings

log = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# Status codes worth retrying — Gemini emits 503 on demand spikes and 429
# when the per-minute quota is breached. 500 sometimes accompanies transient
# upstream failures and is also safe to retry idempotently.
_RETRY_STATUS = {429, 500, 503}
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_S = 1.5


class LLMError(RuntimeError):
    """Raised when the LLM call fails or the response can't be interpreted."""


def _call(
    *,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
) -> str:
    s = settings()
    url = f"{_BASE_URL}/{s.gemini_model}:generateContent"
    # Pass the key via header (not query param) so it doesn't appear in
    # httpx access logs.
    headers = {"x-goog-api-key": s.gemini_api_key}
    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    with httpx.Client(timeout=_TIMEOUT) as client:
        for attempt in range(_MAX_ATTEMPTS):
            is_last = attempt + 1 == _MAX_ATTEMPTS
            try:
                r = client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as e:
                if is_last:
                    raise LLMError(f"LLM transport error after {_MAX_ATTEMPTS} attempts: {e}") from e
                sleep_s = _BACKOFF_BASE_S * (2 ** attempt)
                log.warning("LLM transport error (attempt %d): %s; sleeping %.1fs",
                            attempt + 1, e, sleep_s)
                time.sleep(sleep_s)
                continue

            if r.status_code == 200:
                break

            body = r.text[:500]
            if r.status_code in _RETRY_STATUS and not is_last:
                sleep_s = _BACKOFF_BASE_S * (2 ** attempt)
                log.warning("LLM HTTP %d (attempt %d), sleeping %.1fs",
                            r.status_code, attempt + 1, sleep_s)
                time.sleep(sleep_s)
                continue
            raise LLMError(f"LLM HTTP {r.status_code}: {body}")

    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        # Safety filters or empty generation.
        reason = data.get("promptFeedback", {}).get("blockReason") or "no_candidates"
        raise LLMError(f"LLM returned no candidates ({reason}): {data}")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        finish = candidates[0].get("finishReason", "unknown")
        raise LLMError(f"LLM returned empty text (finish_reason={finish})")
    return text


def complete(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_tokens: int = 1500,
) -> str:
    return _call(
        system=system, user=user,
        temperature=temperature, max_tokens=max_tokens, json_mode=False,
    )


def complete_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.1,
    max_tokens: int = 1500,
) -> Any:
    """Strict JSON output via Gemini's native responseMimeType."""
    raw = _call(
        system=system, user=user,
        temperature=temperature, max_tokens=max_tokens, json_mode=True,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM JSON parse failed: {e}; raw={raw[:200]!r}") from e
