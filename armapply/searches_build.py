"""Build search configuration for a user.

Returns a dict (stored in user preferences in Supabase)
instead of writing to filesystem YAML.
"""

from __future__ import annotations


def build_search_config(
    queries_en: list[str],
    queries_hy: list[str],
    locations: list[dict] | None,
    linkedin: bool,
    staff_am_enabled: bool,
    indeed: bool = False,
    country: str = "worldwide",
    telegram_channels: list[str] | None = None,
) -> dict:
    """Build a search configuration dict for discovery.

    Returns:
        A dict suitable for storing in user preferences and passing to
        discovery.run_full_discovery().
    """
    queries: list[dict] = []
    tier = 1
    for q in queries_en:
        q = (q or "").strip()
        if not q:
            continue
        queries.append({"query": q, "tier": min(tier, 3)})
        tier += 1
    for q in queries_hy:
        q = (q or "").strip()
        if not q:
            continue
        queries.append({"query": q, "tier": min(tier, 3)})
        tier += 1

    if not queries:
        queries = [{"query": "software developer", "tier": 1}]

    locs = locations or [
        {"location": "Remote", "remote": True},
        {"location": "Yerevan, Armenia", "remote": False},
    ]

    # Priority: staff.am (Armenia-native) first, LinkedIn second
    sites: list[str] = []
    if indeed:
        sites.append("indeed")
    if linkedin:
        sites.append("linkedin")

    cfg = {
        "queries": queries,
        "locations": locs,
        "location_accept": [
            "yerevan", "armenia", "remote", "հայաստան",
            "anywhere", "distributed", "worldwide",
        ],
        "location_reject_non_remote": [],
        "country": "worldwide",
        "sites": sites if sites else ["linkedin"],
        "defaults": {"results_per_site": 60, "hours_old": 168, "country_indeed": "usa"},
        "staff_am": {"enabled": staff_am_enabled, "max_pages_per_keyword": 3, "extra_keywords": []},
        "telegram_channels": {
            "enabled": bool(telegram_channels),
            "channels": telegram_channels or [],
            "max_pages_per_channel": 2,
            "keyword_filter": [],
        },
        "exclude_titles": [],
    }
    return cfg
