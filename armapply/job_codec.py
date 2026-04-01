"""Map job URL <-> API job_id (URL-safe)."""

from __future__ import annotations

import base64
from urllib.parse import quote, unquote


def encode_job_id(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def decode_job_id(job_id: str) -> str:
    pad = "=" * (-len(job_id) % 4)
    return base64.urlsafe_b64decode((job_id + pad).encode("ascii")).decode("utf-8")


def try_decode_job_id(job_id: str) -> str:
    """Accept encoded id or raw URL (quoted)."""
    try:
        return decode_job_id(job_id)
    except Exception:
        return unquote(job_id)
