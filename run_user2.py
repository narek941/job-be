import json
import os
import sys
from armapply.users_db import get_user_by_id, update_user_preferences, save_user_resume, update_user_telegram, _exec
from armapply.scheduler import run_pipeline_for_user

uid = 2
u = get_user_by_id(uid)
if not u:
    print(f"User {uid} not found in DB.")
    sys.exit(1)

print(f"User 2 email: {u['email']}")

chat_id = u.get("telegram_chat_id")
prefs = json.loads(u.get("preferences_json") or "{}")

if not chat_id and not prefs.get("telegram_chat_id"):
    print("Warning: No telegram_chat_id found. Notification might fail to send.")

# Ensure they have a valid bot token to send
if not prefs.get("telegram_bot_token"):
    prefs["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN")
    update_user_preferences(uid, prefs)

print("Starting pipeline execution for user 2...")
res = run_pipeline_for_user(uid)
print("\n--- PIPELINE RESULT ---")
print(json.dumps(res, indent=2))

print("\n--- TOP SCORED JOB ---")
row = _exec("SELECT title, site, location, fit_score, cover_letter_text FROM jobs WHERE user_id = %s AND cover_letter_text IS NOT NULL ORDER BY fit_score DESC LIMIT 1", (uid,), fetch="one")
if row:
    print(f"Title: {row.get('title')}")
    print(f"Company: {row.get('site')}")
    print(f"Score: {row.get('fit_score')}/10")
    print(f"Cover Letter Preview:\n{row.get('cover_letter_text')[:300]}...\n")
else:
    print("No jobs scored with cover letters.")
