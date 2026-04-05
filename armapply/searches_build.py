from __future__ import annotations

from pathlib import Path

import yaml


def write_searches_for_user(
    user_root: Path,
    queries_en: list[str],
    queries_hy: list[str],
    locations: list[dict] | None,
    linkedin: bool,
    staff_am_enabled: bool,
    indeed: bool = False,
    country: str = "worldwide",   # 'ARM' is not accepted by jobspy; staff.am handles Armenia natively
) -> None:
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
        {"location": "Remote", "remote": True},          # remote first — broader results
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
            "yerevan",
            "armenia",
            "remote",
            "հայաստան",
            "anywhere",
            "distributed",
            "worldwide",
        ],
        "location_reject_non_remote": [],
        "country": "worldwide",   # jobspy valid value; staff.am ignores this
        "sites": sites if sites else ["linkedin"],
        "defaults": {"results_per_site": 60, "hours_old": 168, "country_indeed": "usa"},
        # staff.am runs FIRST (handled by discovery layer before jobspy)
        "staff_am": {"enabled": staff_am_enabled, "max_pages_per_keyword": 3, "extra_keywords": []},
        "workday": {"enabled": False},
        "smartextract": {"enabled": False},
        "exclude_titles": [],
    }
    (user_root / "searches.yaml").write_text(yaml.dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
