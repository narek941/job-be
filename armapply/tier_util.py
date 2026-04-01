"""Feature gating aligned with ApplyPilot tiers (without SystemExit)."""

from __future__ import annotations


def current_tier() -> int:
    import applypilot.config as ap_cfg

    ap_cfg.load_env()
    from applypilot.config import get_tier

    return get_tier()


def require_tier(min_tier: int, feature: str) -> None:
    from fastapi import HTTPException

    t = current_tier()
    if t < min_tier:
        raise HTTPException(
            status_code=503,
            detail=f"{feature} requires ApplyPilot tier {min_tier}. "
            f"Configure LLM keys in workspace .env (tier 2+) and Claude Code + Chrome for auto-apply (tier 3).",
        )
