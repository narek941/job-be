#!/usr/bin/env bash
# daily_pipeline.sh
# Runs the full ArmApply pipeline daily via cron:
#   1. staff.am scan
#   2. LinkedIn scan
#   3. Score new jobs
#   4. Send top matches to Telegram for approval
#
# Cron setup (runs every day at 8:00 AM):
#   crontab -e
#   0 8 * * * /path/to/armapply-backend/daily_pipeline.sh >> /path/to/armapply-backend/logs/daily.log 2>&1

set -euo pipefail
cd "$(dirname "$0")"

# Load env
if [[ -f .env.test ]]; then
  set -a
  source .env.test
  set +a
fi

if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi
export PYTHONPATH="$(pwd)"

LOG_DATE=$(date '+%Y-%m-%d %H:%M:%S')
echo ""
echo "════════════════════════════════════════"
echo " ArmApply Daily Pipeline — $LOG_DATE"
echo "════════════════════════════════════════"

python3 - <<'PYEOF'
import requests, json, sys, time, os

BASE = os.environ.get("ARMAPPLY_API_URL", "http://localhost:8000")

# Login
try:
    import os
    email    = os.environ.get("ARMAPPLY_TEST_USER_EMAIL",    "test@test.com")
    password = os.environ.get("ARMAPPLY_TEST_USER_PASSWORD", "TestPass123")
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password}, timeout=15)
    r.raise_for_status()
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    print(f"[OK] Logged in as {email}")
except Exception as e:
    print(f"[FAIL] Login failed: {e}")
    sys.exit(1)

# ── Step 1: Update searches (staff.am only for fast test) ─────────────────
try:
    prefs = requests.get(f"{BASE}/settings/preferences", headers=H, timeout=15).json()
    r = requests.post(f"{BASE}/search-jobs", headers=H, json={
        "queries_en": ["Frontend Engineer"], # Single fast query
        "queries_hy": [],
        "linkedin":   False, # Disable LinkedIn for testing speed
        "staff_am":   True,
        "indeed":     False,
        "workers":    1,
    }, timeout=600)
    result = r.json()
    new_c  = result.get("result", {}).get("supabase_sync", {}).get("new", 0)
    print(f"[OK] Discovery done — {new_c} new jobs found")
except Exception as e:
    print(f"[WARN] Discovery error: {e}")

# Small pause between pipeline steps
time.sleep(5)

# ── Step 2: Score jobs ───────────────────────────────────────────────────────
try:
    r = requests.post(f"{BASE}/score-jobs", headers=H, json={"limit": 30}, timeout=600)
    scored = r.json().get("result", {}).get("scored", 0)
    print(f"[OK] Scoring done — {scored} jobs scored")
except Exception as e:
    print(f"[WARN] Scoring error: {e}")

time.sleep(5)

# ── Step 3: Approval pipeline (Telegram → Gmail) ─────────────────────────────
try:
    recipient = prefs.get("application_email", "")
    if not recipient:
        print("[SKIP] No application_email set — skipping approval pipeline")
    else:
        r = requests.post(f"{BASE}/pipeline/run-approval", headers=H, json={
            "min_score":       7,
            "max_jobs":        3,
            "recipient_email": recipient,
        }, timeout=600)
        res = r.json()
        sent = res.get("result", {}).get("sent", 0)
        print(f"[OK] Approval pipeline done — {sent} Telegram message(s) sent")
except Exception as e:
    print(f"[WARN] Approval pipeline error: {e}")

print("[DONE] Daily pipeline complete.")
PYEOF
