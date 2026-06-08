"""Single-provider LLM client (Gemini via the OpenAI-compatible endpoint).

Returns plain strings or parsed JSON objects. No streaming, no tool-use —
this codebase only needs one-shot completions.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from armapply.config import settings

log = logging.getLogger(__name__)

# Gemini exposes an OpenAI-compatible endpoint.
_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class LLMError(RuntimeError):
    """Raised when the LLM call fails after retries or the response can't be parsed."""


def complete(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_tokens: int = 1500,
) -> str:
    """Return the model's plain-text completion."""
    s = settings()
    headers = {"Authorization": f"Bearer {s.gemini_api_key}"}
    payload = {
        "model": s.gemini_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.post(f"{_BASE_URL}/chat/completions", headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise LLMError(f"LLM HTTP error: {e}") from e

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError) as e:
        raise LLMError(f"Malformed LLM response: {data!r}") from e


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)
_JSON_FALLBACK_RE = re.compile(r"(\{.*\}|\[.*\])", re.S)


def complete_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.1,
    max_tokens: int = 1500,
) -> Any:
    """Like `complete`, but expects strict JSON output and parses it.

    Tolerates ```json``` fenced blocks and stray prose around the JSON, but
    fails fast if no parseable object is found.
    """
    raw = complete(
        system=system + "\n\nReply with valid JSON only — no commentary, no markdown.",
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return _parse_json(raw)


def _parse_json(raw: str) -> Any:
    raw = raw.strip()
    # Try direct parse first; it succeeds when the model behaved.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(raw) or _JSON_FALLBACK_RE.search(raw)
    if not m:
        raise LLMError(f"LLM did not return JSON: {raw[:200]!r}")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM JSON parse failed: {e}; raw={raw[:200]!r}") from e
