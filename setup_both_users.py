"""One-time script to configure BOTH users in Supabase for the daily pipeline.

Sets up for each user:
  - telegram_chat_id + telegram_bot_token in preferences_json
  - search_config in preferences_json
  - profile_data in preferences_json
  - resume_text in users table

Run:  set -a && source .env.test && set +a && .venv/bin/python3 setup_both_users.py
"""

import json
import os
import sys

# Must be run from armapply-backend/
sys.path.insert(0, os.path.dirname(__file__))

from armapply.users_db import (
    get_user_by_id,
    get_user_preferences,
    update_user_preferences,
    update_user_telegram,
    save_user_resume,
    init_app_db,
)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not set. Source .env.test first.")
    sys.exit(1)

init_app_db()

# ─── User 2: Narek ──────────────────────────────────────────────────────────

def setup_user_2():
    uid = 2
    user = get_user_by_id(uid)
    if not user:
        print(f"ERROR: User {uid} not found in DB!")
        return False

    print(f"\n=== Setting up User {uid}: {user['email']} ===")

    # 1. Resume — use cv_template since user 2's resume.txt is placeholder
    from armapply.cv_template import render_cv_text
    resume = render_cv_text()
    save_user_resume(uid, resume)
    print(f"  ✓ Resume saved ({len(resume)} chars)")

    # 2. Telegram chat ID — you need to message the bot first, then check
    #    For now, set the chat_id from the column if it exists, or prompt
    prefs = get_user_preferences(uid)
    chat_id = user.get("telegram_chat_id") or prefs.get("telegram_chat_id")

    if not chat_id:
        print("  ⚠ No telegram_chat_id found for User 2.")
        print("    → Message your bot on Telegram, then run:")
        print(f"    curl https://api.telegram.org/bot{BOT_TOKEN}/getUpdates")
        print("    → Find your chat.id and set it below:")
        chat_id = input("    Enter Narek's telegram chat_id (or press Enter to skip): ").strip()
        if not chat_id:
            print("  ⚠ Skipping Telegram for User 2")

    if chat_id:
        update_user_telegram(uid, str(chat_id))
        prefs["telegram_chat_id"] = str(chat_id)
        print(f"  ✓ telegram_chat_id = {chat_id}")

    # 3. Bot token
    prefs["telegram_bot_token"] = BOT_TOKEN
    print(f"  ✓ telegram_bot_token set")

    # 4. Search config (from data/users/2/searches.yaml)
    search_config = {
        "queries": [
            {"query": "senior frontend engineer", "tier": 1},
            {"query": "react developer", "tier": 1},
            {"query": "react native engineer", "tier": 1},
            {"query": "senior react developer", "tier": 2},
            {"query": "fullstack developer react", "tier": 2},
            {"query": "frontend architect", "tier": 2},
        ],
        "locations": [
            {"location": "Yerevan, Armenia", "remote": False},
            {"location": "Armenia", "remote": False},
            {"location": "Remote", "remote": True},
        ],
        "location_accept": ["yerevan", "armenia", "remote", "հայաստան", "anywhere", "distributed"],
        "location_reject_non_remote": [],
        "country": "worldwide",
        "sites": ["linkedin", "indeed"],
        "defaults": {"results_per_site": 60, "hours_old": 168},
        "staff_am": {"enabled": True, "max_pages_per_keyword": 2, "extra_keywords": []},
        "telegram_channels": {
            "enabled": True,
            "max_pages_per_channel": 3,
            "channels": ["rabotadlaqa", "freeIT_job", "vgrecruitingit", "jun_qa"],
            "keyword_filter": [
                "armenia", "yerevan", "remote", "developer", "engineer",
                "python", "backend", "frontend", "vacancy", "job",
            ],
        },
        "exclude_titles": [],
    }
    prefs["search_config"] = search_config
    print(f"  ✓ search_config set ({len(search_config['queries'])} queries)")

    # 5. Profile data (from data/users/2/profile.json)
    with open(os.path.join(os.path.dirname(__file__), "data/users/2/profile.json")) as f:
        profile_data = json.load(f)
    prefs["profile_data"] = profile_data
    print(f"  ✓ profile_data loaded")

    # 6. Auto-pilot
    prefs["auto_pilot"] = True
    print(f"  ✓ auto_pilot = True")

    update_user_preferences(uid, prefs)
    print(f"  ✓ All preferences saved for User {uid}")
    return True


# ─── User 3: Laura ──────────────────────────────────────────────────────────

def setup_user_3():
    uid = 3
    user = get_user_by_id(uid)
    if not user:
        print(f"ERROR: User {uid} not found in DB!")
        return False

    print(f"\n=== Setting up User {uid}: {user['email']} ===")

    # 1. Resume — Laura has a real one
    resume_path = os.path.join(os.path.dirname(__file__), "data/users/3/resume.txt")
    with open(resume_path) as f:
        resume = f.read()
    save_user_resume(uid, resume)
    print(f"  ✓ Resume saved ({len(resume)} chars)")

    # 2. Telegram chat ID
    prefs = get_user_preferences(uid)
    chat_id = user.get("telegram_chat_id") or prefs.get("telegram_chat_id")

    if not chat_id:
        print("  ⚠ No telegram_chat_id found for User 3.")
        print("    → Laura needs to message the bot on Telegram first.")
        chat_id = input("    Enter Laura's telegram chat_id (or press Enter to skip): ").strip()
        if not chat_id:
            print("  ⚠ Skipping Telegram for User 3")

    if chat_id:
        update_user_telegram(uid, str(chat_id))
        prefs["telegram_chat_id"] = str(chat_id)
        print(f"  ✓ telegram_chat_id = {chat_id}")

    # 3. Bot token
    prefs["telegram_bot_token"] = BOT_TOKEN
    print(f"  ✓ telegram_bot_token set")

    # 4. Search config (from data/users/3/searches.yaml)
    search_config = {
        "queries": [
            {"query": "QA Intern", "tier": 1},
            {"query": "Junior QA Engineer", "tier": 1},
            {"query": "Manual QA", "tier": 1},
            {"query": "Quality Assurance Engineer", "tier": 2},
            {"query": "Data Analyst Junior", "tier": 2},
            {"query": "Junior Software Tester", "tier": 2},
        ],
        "locations": [
            {"location": "Yerevan, Armenia", "remote": False},
            {"location": "Remote", "remote": True},
        ],
        "location_accept": ["yerevan", "armenia", "remote", "հայաստան", "anywhere", "distributed", "worldwide"],
        "location_reject_non_remote": [],
        "country": "worldwide",
        "sites": ["linkedin"],
        "defaults": {"results_per_site": 60, "hours_old": 168, "country_indeed": "usa"},
        "staff_am": {"enabled": True, "max_pages_per_keyword": 3, "extra_keywords": []},
        "telegram_channels": {
            "enabled": True,
            "max_pages_per_channel": 3,
            "channels": ["rabotadlaqa", "freeIT_job", "vgrecruitingit", "jun_qa"],
            "keyword_filter": [
                "armenia", "yerevan", "remote", "qa", "quality assurance",
                "tester", "analyst", "vacancy", "job",
            ],
        },
    }
    prefs["search_config"] = search_config
    print(f"  ✓ search_config set ({len(search_config['queries'])} queries)")

    # 5. Profile data (from data/users/3/profile.json)
    with open(os.path.join(os.path.dirname(__file__), "data/users/3/profile.json")) as f:
        profile_data = json.load(f)
    prefs["profile_data"] = profile_data
    print(f"  ✓ profile_data loaded")

    # 6. Auto-pilot
    prefs["auto_pilot"] = True
    print(f"  ✓ auto_pilot = True")

    update_user_preferences(uid, prefs)
    print(f"  ✓ All preferences saved for User {uid}")
    return True


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ok1 = setup_user_2()
    ok2 = setup_user_3()

    print("\n" + "=" * 60)
    if ok1 and ok2:
        print("✅ Both users configured! Pipeline will process them on next cron run.")
    else:
        print("⚠ Some users were not configured. Check errors above.")

    print("\nTo verify, run:")
    print("  set -a && source .env.test && set +a && .venv/bin/python3 -c \"")
    print("  from armapply.users_db import get_users_with_autopilot")
    print("  users = get_users_with_autopilot()")
    print("  for u in users: print(f'  User {u[\\\"id\\\"]}: {u[\\\"email\\\"]}')\"")
