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
from typing import Any

import httpx

from armapply.config import settings

log = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


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
    params = {"key": s.gemini_api_key}
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

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(url, params=params, json=payload)
    except httpx.HTTPError as e:
        raise LLMError(f"LLM transport error: {e}") from e

    if r.status_code != 200:
        body = r.text[:500]
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
