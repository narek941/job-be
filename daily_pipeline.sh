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

BASE = os.environ.get("ARMAPPLY_API_URL", "https://prod-job-apply.onrender.com")

# Run for all production users
USERS = [
    {"email": "narek@armapply.am", "pass": "NarekPass321"}, # Update if actual pass is different
    {"email": "arakelyanlaura0@gmail.com", "pass": "LauraPass123!"}
]

for user_info in USERS:
    print(f"\n--- Processing User: {user_info['email']} ---")
    try:
        r = requests.post(f"{BASE}/auth/login", json={"email": user_info["email"], "password": user_info["pass"]}, timeout=15)
        r.raise_for_status()
        token = r.json()["access_token"]
        H = {"Authorization": f"Bearer {token}"}
        print(f"[OK] Logged in successfully")
    except Exception as e:
        print(f"[FAIL] Login failed for {user_info['email']}: {e}")
        continue

    # ── Step 1: Update searches (staff.am + linkedin + telegram) ─────────────────
    try:
        # Load searches.yaml defaults for discovery
        r = requests.post(f"{BASE}/search-jobs", headers=H, json={
            "queries_en": [], # Use searches.yaml
            "queries_hy": [],
            "linkedin":   True,
            "staff_am":   True,
            "indeed":     True,
            "workers":    2,
        }, timeout=1200)
        print(f"[OK] Discovery triggered")
    except Exception as e:
        print(f"[WARN] Discovery error: {e}")

    # Small pause
    time.sleep(2)

    # ── Step 2: Score jobs ───────────────────────────────────────────────────────
    try:
        r = requests.post(f"{BASE}/score-jobs", headers=H, json={"limit": 50}, timeout=600)
        print(f"[OK] Scoring triggered")
    except Exception as e:
        print(f"[WARN] Scoring error: {e}")

    time.sleep(2)

    # ── Step 3: Approval pipeline ────────────────────────────────────────────────
    try:
        prefs = requests.get(f"{BASE}/settings/preferences", headers=H, timeout=15).json()
        recipient = prefs.get("application_email", user_info["email"])
        r = requests.post(f"{BASE}/pipeline/run-approval", headers=H, json={
            "min_score":       7,
            "max_jobs":        5,
            "recipient_email": recipient,
        }, timeout=600)
        print(f"[OK] Telegram notifications sent")
    except Exception as e:
        print(f"[WARN] Approval pipeline error: {e}")

print("\n[DONE] Daily pipeline complete for all users.")
PYEOF
