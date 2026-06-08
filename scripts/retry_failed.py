"""Reset any jobs stuck in status='failed' back to 'new' so they get
re-scored on the next pipeline run.

Useful after a transient outage (LLM 4xx/5xx, network blip). Idempotent.

Usage:
    python -m scripts.retry_failed
"""

from __future__ import annotations

import logging

from armapply.db import query, run_migrations

log = logging.getLogger("retry_failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_migrations()
    n = query(
        "UPDATE jobs SET status = 'new', apply_error = NULL WHERE status = 'failed'"
    )
    log.info("Reset %s failed jobs to 'new'", n)


if __name__ == "__main__":
    main()
