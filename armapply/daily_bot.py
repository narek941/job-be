"""ArmApply Daily Bot - Cron script for Auto-Pilot automation."""

import argparse
import asyncio
import logging
from applypilot.database import get_connection
from armapply.config import DATA_ROOT
from armapply.users_db import init_app_db, list_pipeline_runs, log_pipeline_run, _conn, get_user_preferences
from armapply.workspace import run_in_pipeline
from armapply.pipeline_ops import (
    discover_and_enrich,
    score_jobs_batch,
    tailor_job_by_url,
    cover_letter_for_job,
    run_auto_apply,
)
from armapply.searches_build import write_searches_for_user

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("daily_bot")

async def run_autopilot_for_user(uid: int, prefs: dict, dry_run: bool):
    log.info(f"Running autopilot for user {uid}")
    
    # 1. Provide default searches if none
    def _prepare_searches():
        from armapply.workspace import ensure_user_workspace
        root = ensure_user_workspace(uid)
        queries = prefs.get("target_roles", ["Software Engineer"])
        location = prefs.get("target_location", "Armenia")
        write_searches_for_user(
            root,
            queries_en=queries,
            queries_hy=[],
            locations=[{"location": location, "remote": False}, {"location": "Remote", "remote": True}],
            linkedin=True,
            staff_am_enabled=True,
            indeed=False,
            country="ARM"
        )
    await run_in_pipeline(uid, _prepare_searches)

    # 2. Discover & Enrich
    def _discover():
        return discover_and_enrich(workers=1)
    
    res = await run_in_pipeline(uid, _discover)
    log.info(f"[User {uid}] Discover: {res}")
    
    # 3. Score Jobs
    def _score():
        return score_jobs_batch(limit=50)
    
    res = await run_in_pipeline(uid, _score)
    log.info(f"[User {uid}] Score: {res}")
    
    # 4. Fetch high-scoring jobs not applied yet
    def _fetch_actionable_jobs():
        conn = get_connection()
        rows = conn.execute(
            "SELECT url FROM jobs WHERE fit_score >= 7 AND applied_at IS NULL ORDER BY fit_score DESC LIMIT 5"
        ).fetchall()
        return [r["url"] for r in rows]
    
    jobs = await run_in_pipeline(uid, _fetch_actionable_jobs)
    log.info(f"[User {uid}] Found {len(jobs)} highly scored jobs to process.")
    
    # 5. Tailor, Cover Letter & Apply
    for url in jobs:
        try:
            log.info(f"[User {uid}] Processing job: {url}")
            
            # Tailor
            def _tailor():
                return tailor_job_by_url(url, min_score=7, validation_mode="normal")
            await run_in_pipeline(uid, _tailor)
            
            # Cover Letter
            def _cl():
                return cover_letter_for_job(url, validation_mode="normal")
            await run_in_pipeline(uid, _cl)
            
            # Apply
            def _apply():
                run_auto_apply(job_url=url, min_score=7, dry_run=dry_run, headless=True)
            await run_in_pipeline(uid, _apply)
            
            log_pipeline_run(uid, "bot_apply", "ok", f"dry_run={dry_run} url={url[:200]}")
            
        except Exception as e:
            log.error(f"[User {uid}] Failed on job {url}: {e}")
            log_pipeline_run(uid, "bot_apply", "error", str(e)[:1000])

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't actually submit applications.")
    args = parser.parse_args()

    init_app_db()
    conn = _conn()
    users = conn.execute("SELECT id, email FROM users").fetchall()
    
    print(f"DEBUG: Found {len(users)} users in DB")
    log.info(f"Starting daily bot for {len(users)} users. Dry run: {args.dry_run}")
    
    for row in users:
        uid = row["id"]
        prefs = get_user_preferences(uid)
        
        # Check if autopilot is enabled
        auto_pilot = prefs.get("auto_pilot", False)
        if not auto_pilot:
            log.info(f"Skipping user {uid} ({row['email']}) - Autopilot Disabled")
            continue
            
        try:
            await run_autopilot_for_user(uid, prefs, dry_run=args.dry_run)
        except Exception as e:
            log.error(f"Error processing autopilot for user {uid}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
