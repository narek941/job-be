"""Drop the legacy ArmApply v1 tables.

Destructive. Run this exactly once when switching to v2. Idempotent — running
it again after v2 is in place is a no-op (the legacy-only tables won't exist).

After dropping, call `armapply.db.run_migrations()` (the API does this at
startup) to create the v2 schema.

Usage:
    python -m scripts.reset_legacy
"""

from __future__ import annotations

import logging

from armapply.db import transaction

log = logging.getLogger("reset_legacy")

# Order matters: child tables first so FK drops are clean.
LEGACY_TABLES = (
    "calendar_events",
    "api_logs",
    "pipeline_runs",
    "jobs",
    "users",
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    with transaction() as cur:
        for tbl in LEGACY_TABLES:
            log.info("DROP TABLE IF EXISTS %s CASCADE", tbl)
            cur.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
        # schema_migrations is owned by v2 — drop it too so run_migrations()
        # re-applies version 1 from scratch.
        cur.execute("DROP TABLE IF EXISTS schema_migrations CASCADE")
    log.info("Legacy tables dropped. Run the app to create v2 schema.")


if __name__ == "__main__":
    main()
