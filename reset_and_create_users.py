"""Reset DB and create 2 users via Supabase REST API (bypasses stuck pooler)."""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import httpx
from armapply.auth_deps import hash_password

# ── Config ───────────────────────────────────────────────────────────────
SUPABASE_URL = "https://sgmbcveoxfkgcmfkvxuh.supabase.co"
SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNnbWJjdmVveGZrZ2NtZmt2eHVoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTA3MDY3MywiZXhwIjoyMDkwNjQ2NjczfQ.WMhQ5HcK3TLn6thb1V9M8lNOnrz_s9bxgndTPHh_xAQ"

HEADERS = {
    "apikey": SERVICE_KEY,
    "Authorization": f"Bearer {SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

NAREK_BOT_TOKEN = "8631880764:AAHtiVHP3svKqAEUXWboDWiDQEcFWauYWFE"
LAURA_BOT_TOKEN = "8649332561:AAFDYA3X40O4vj9tADCPMogwcov9FEQPwI8"
NAREK_CHAT_ID = "452020275"

client = httpx.Client(base_url=f"{SUPABASE_URL}/rest/v1", headers=HEADERS, timeout=30)


def api(method, path, **kwargs):
    r = getattr(client, method)(path, **kwargs)
    if r.status_code >= 400:
        print(f"    ERR {r.status_code}: {r.text[:200]}")
    return r


def get_narek_resume():
    from armapply.cv_template import render_cv_text
    return render_cv_text()


def get_laura_resume():
    with open(os.path.join(os.path.dirname(__file__), "data/users/3/resume.txt")) as f:
        return f.read()


def get_profile(user_num):
    with open(os.path.join(os.path.dirname(__file__), f"data/users/{user_num}/profile.json")) as f:
        return json.load(f)


def main():
    now = datetime.now(timezone.utc).isoformat()

    # ── Step 1: Check existing users ─────────────────────────────────────
    print("Testing Supabase REST API...")
    r = api("get", "/users?select=id,email&order=id")
    existing = r.json()
    print(f"  Connected! {len(existing)} existing users\n")

    # ── Step 2: Delete everything (correct order for FKs) ────────────────
    print("=== CLEANING UP ===")

    # Jobs has composite PK (user_id, url) — filter by user_id
    for u in existing:
        uid = u["id"]
        api("delete", f"/jobs?user_id=eq.{uid}")
        api("delete", f"/pipeline_runs?user_id=eq.{uid}")
        api("delete", f"/calendar_events?user_id=eq.{uid}")
    print("  ✓ jobs, pipeline_runs, calendar_events cleared")

    # api_logs might have user_id or NULL
    api("delete", "/api_logs?id=gt.0")
    print("  ✓ api_logs cleared")

    # Now delete users (FKs are gone)
    for u in existing:
        api("delete", f"/users?id=eq.{u['id']}")
    print(f"  ✓ {len(existing)} users deleted\n")

    # ── Step 3: Create User 1 — Narek ────────────────────────────────────
    print("=== CREATING USER 1: NAREK ===")

    narek_prefs = {
        "auto_pilot": True,
        "telegram_chat_id": NAREK_CHAT_ID,
        "telegram_bot_token": NAREK_BOT_TOKEN,
        "profile_data": get_profile(2),
        "search_config": {
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
            "location_accept": ["yerevan", "armenia", "remote", "anywhere", "distributed"],
            "location_reject_non_remote": [],
            "country": "worldwide",
            "sites": ["linkedin", "indeed"],
            "defaults": {"results_per_site": 60, "hours_old": 168},
            "staff_am": {"enabled": True, "max_pages_per_keyword": 2, "extra_keywords": []},
            "telegram_channels": {
                "enabled": True,
                "max_pages_per_channel": 3,
                "channels": ["rabotadlaqa", "freeIT_job", "vgrecruitingit", "jun_qa"],
                "keyword_filter": ["armenia", "yerevan", "remote", "developer", "engineer", "frontend", "vacancy", "job"],
            },
            "exclude_titles": [],
        },
    }

    narek_resume = get_narek_resume()
    r = api("post", "/users", json={
        "email": "nqolyan@gmail.com",
        "password_hash": hash_password("TestPass123"),
        "telegram_chat_id": NAREK_CHAT_ID,
        "preferences_json": json.dumps(narek_prefs),
        "resume_text": narek_resume,
        "created_at": now,
    })

    if r.status_code < 300:
        narek = r.json()[0]
        print(f"  ✓ User {narek['id']}: nqolyan@gmail.com / TestPass123")
        print(f"  ✓ Telegram: chat_id={NAREK_CHAT_ID}, bot=Narek's bot")
        print(f"  ✓ Resume: {len(narek_resume)} chars")
        print(f"  ✓ 6 search queries, auto_pilot=True")
    else:
        print("  FAILED!")
        return

    # ── Step 4: Create User 2 — Laura ────────────────────────────────────
    print("\n=== CREATING USER 2: LAURA ===")

    laura_prefs = {
        "auto_pilot": True,
        "telegram_chat_id": None,
        "telegram_bot_token": LAURA_BOT_TOKEN,
        "profile_data": get_profile(3),
        "search_config": {
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
            "location_accept": ["yerevan", "armenia", "remote", "anywhere", "distributed", "worldwide"],
            "location_reject_non_remote": [],
            "country": "worldwide",
            "sites": ["linkedin"],
            "defaults": {"results_per_site": 60, "hours_old": 168},
            "staff_am": {"enabled": True, "max_pages_per_keyword": 3, "extra_keywords": []},
            "telegram_channels": {
                "enabled": True,
                "max_pages_per_channel": 3,
                "channels": ["rabotadlaqa", "freeIT_job", "vgrecruitingit", "jun_qa"],
                "keyword_filter": ["armenia", "yerevan", "remote", "qa", "quality assurance", "tester", "analyst", "vacancy", "job"],
            },
        },
    }

    laura_resume = get_laura_resume()
    r = api("post", "/users", json={
        "email": "arakelyanlaura0@gmail.com",
        "password_hash": hash_password("TestPass123"),
        "telegram_chat_id": None,
        "preferences_json": json.dumps(laura_prefs),
        "resume_text": laura_resume,
        "created_at": now,
    })

    if r.status_code < 300:
        laura = r.json()[0]
        print(f"  ✓ User {laura['id']}: arakelyanlaura0@gmail.com / TestPass123")
        print(f"  ✓ Telegram: @laura_arakelyan_jobs_bot (chat_id pending)")
        print(f"  ✓ Resume: {len(laura_resume)} chars")
        print(f"  ✓ 6 search queries, auto_pilot=True")
    else:
        print("  FAILED!")
        return

    # ── Step 5: Verify ───────────────────────────────────────────────────
    print("\n=== VERIFICATION ===")
    r = api("get", "/users?select=id,email,telegram_chat_id&order=id")
    for u in r.json():
        print(f"  User {u['id']}: {u['email']} | chat_id={u['telegram_chat_id']}")

    # ── Step 6: Test Telegram ────────────────────────────────────────────
    print("\n=== TEST TELEGRAM ===")
    tg = httpx.post(
        f"https://api.telegram.org/bot{NAREK_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": NAREK_CHAT_ID,
            "text": (
                "✅ *ArmApply DB reset!*\n\n"
                f"👤 User {narek['id']}: Narek\n"
                f"👤 User {laura['id']}: Laura\n\n"
                "🔍 Pipeline ready!"
            ),
            "parse_mode": "Markdown",
        },
        timeout=10,
    )
    print(f"  Narek: {'✓ sent' if tg.status_code == 200 else '✗ ' + tg.text[:100]}")

    print("\n" + "=" * 60)
    print("✅ DONE!")
    print(f"\n  User {narek['id']}: nqolyan@gmail.com / TestPass123")
    print(f"  User {laura['id']}: arakelyanlaura0@gmail.com / TestPass123")
    print("\n⚠ Laura → /start @laura_arakelyan_jobs_bot for notifications")


if __name__ == "__main__":
    main()
