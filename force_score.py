import os
import json
import logging
from armapply.users_db import _exec, get_user_preferences, update_job_field
from armapply.scoring import score_jobs_for_user
from armapply.llm_features import generate_tailored_cover_letter
from armapply.telegram_notify import send_job_match_notification

logging.basicConfig(level=logging.INFO)

uid = 2
print("1. Forcing AI scoring on 5 unscored jobs...")
score_res = score_jobs_for_user(uid, limit=5)
print(f"Scoring result: {score_res}")

print("2. Generating cover letters for high scoring jobs (score >= 7)...")
rows = _exec(
    "SELECT url, title, site, location, description, full_description, fit_score, score_reasoning, salary "
    "FROM jobs WHERE user_id = %s AND fit_score >= 7 AND cover_letter_text IS NULL "
    "ORDER BY fit_score DESC LIMIT 5",
    (uid,), fetch="all"
)

if not rows:
    print("No jobs scored 7 or higher. Here are the top 5 scores we got:")
    top_5 = _exec("SELECT title, site, fit_score, score_reasoning FROM jobs WHERE user_id = %s AND fit_score IS NOT NULL ORDER BY fit_score DESC LIMIT 5", (uid,), fetch="all")
    for r in top_5:
        print(f"[{r['fit_score']}/10] {r['title']} @ {r['site']}\nReason: {r['score_reasoning'][:100]}...\n")
else:
    prefs = get_user_preferences(uid)
    chat_id = prefs.get("telegram_chat_id")
    bot_token = prefs.get("telegram_bot_token")
    
    for row in rows:
        print(f"Generating cover letter for {row['title']} (Score: {row['fit_score']})")
        cover = generate_tailored_cover_letter(dict(row))
        update_job_field(uid, row["url"], "cover_letter_text", cover)
        
        if chat_id and bot_token:
            print("Sending to Telegram...")
            send_job_match_notification(str(chat_id), dict(row), cover, bot_token)
            update_job_field(uid, row["url"], "agent_id", "notified")

print("Done!")
