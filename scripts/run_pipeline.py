"""Daily pipeline entry point for the Render cron job.

Runs `pipeline.run_all()` once and exits. The cron job's Docker container
is the same image as the web service, so the env vars (DATABASE_URL,
GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, GEMINI_MODEL) must be configured on
the cron service exactly as on the web service.

PIPELINE_SECRET is NOT required here — we bypass the HTTP /cron endpoint
and call the pipeline functions directly.

Usage (configured in Render → Cron Job → Command):
    python -m scripts.run_pipeline
"""

from __future__ import annotations

import logging
import sys

from jobfox import db, pipeline


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("cron")

    db.run_migrations()
    results = pipeline.run_all()
    log.info("daily pipeline complete: %d users processed", len(results))
    # If any user crashed mid-pipeline, exit non-zero so Render flags the run.
    failed = sum(1 for r in results if r.errors)
    if failed:
        log.warning("%d users had errors", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
