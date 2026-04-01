#!/usr/bin/env python3
"""ArmApply Cron Autopilot: Runs the full pipeline for all users daily.

1. Job Discovery (Armenian market + Remote)
2. AI Scoring (Groq)
3. Resume Tailoring (ATS Optimized)
4. Cover Letter Generation (Technical focus)
5. Telegram Notifications
"""

import logging
import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# Fix path to include the current directory so armapply imports work
sys.path.append(str(Path(__file__).parent.parent))

from armapply.users_db import _conn, log_pipeline_run
from armapply.workspace import run_in_pipeline
from armapply.pipeline_ops import (
    discover_and_enrich, 
    score_jobs_batch, 
    tailor_job_by_url, 
    cover_letter_for_job
)
from armapply.telegram_notify import send_telegram_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("armapply.cron")

def run_for_user(user: dict):
    uid = user["id"]
    email = user["email"]
    chat_id = user.get("telegram_chat_id")
    
    log.info(f"--- Starting Autopilot for {email} (ID: {uid}) ---")
    
    try:
        # 1. Discovery
        def _discover():
            return discover_and_enrich(workers=2)
        
        disc_res = run_in_pipeline(uid, _discover)
        new_jobs = disc_res.get("new_jobs", 0)
        log.info(f"[{email}] Discovery done: {new_jobs} new jobs found.")
        
        # 2. Scoring
        def _score():
            return score_jobs_batch(limit=20)
        
        score_res = run_in_pipeline(uid, _score)
        scored_count = score_res.get("scored", 0)
        log.info(f"[{email}] Scoring done: {scored_count} jobs scored.")
        
        # 3. Tailoring for high-scorers
        def _get_hot_jobs():
            from applypilot.database import get_connection, get_jobs_by_stage
            conn = get_connection()
            return get_jobs_by_stage(conn, stage="pending_tailor", min_score=7, limit=5)
            
        hot_jobs = run_in_pipeline(uid, _get_hot_jobs)
        
        tailored_count = 0
        for job in hot_jobs:
            url = job["url"]
            title = job["title"]
            log.info(f"[{email}] Tailoring for: {title} (Score: {job['fit_score']})")
            
            try:
                # Tailor Resume
                run_in_pipeline(uid, lambda: tailor_job_by_url(url, min_score=7))
                # Generate Cover Letter
                run_in_pipeline(uid, lambda: cover_letter_for_job(url))
                tailored_count += 1
                
                # Notify on Telegram if this is a high-quality match
                if chat_id:
                    msg = (
                        f"🚀 *ArmApply Match Found!*\n\n"
                        f"Job: {title}\n"
                        f"Company: {job['site']}\n"
                        f"Score: *{job['fit_score']}/10*\n\n"
                        f"✅ Tailored Resume & Cover Letter generated.\n"
                        f"Check the app to review and apply!"
                    )
                    send_telegram_message(str(chat_id), msg)
            except Exception as eje:
                log.error(f"[{email}] Failed to tailor {title}: {eje}")

        # 4. Cleanup old un-applied jobs (30 days)
        def _cleanup():
            from applypilot.database import cleanup_old_jobs
            return cleanup_old_jobs(days=30)
            
        deleted_count = run_in_pipeline(uid, _cleanup)
        if deleted_count > 0:
            log.info(f"[{email}] Cleanup done: Removed {deleted_count} stale jobs (>30 days).")

        log.info(f"--- Finished Autopilot for {email}. Tailored {tailored_count} jobs. ---")
        log_pipeline_run(uid, "cron_autopilot", "ok", f"Scored: {scored_count}, Tailored: {tailored_count}, Cleaned: {deleted_count}")

    except Exception as e:
        log.error(f"Critical error in Autopilot for {email}: {e}")
        log_pipeline_run(uid, "cron_autopilot", "error", str(e)[:1000])

def main():
    log.info("ArmApply Cron Autopilot starting...")
    
    # Load all users from Supabase
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute("SELECT id, email, telegram_chat_id FROM users")
        users = cur.fetchall()
    
    if not users:
        log.info("No users found in database.")
        return

    for user in users:
        run_for_user(user)

    log.info("ArmApply Cron Autopilot execution finished.")

if __name__ == "__main__":
    main()
