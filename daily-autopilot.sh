#!/usr/bin/env bash
# ArmApply Daily Autopilot Wrapper
# This script is intended to be run by cron (e.g. daily at 9:00 AM)
# Output is logged to logs/autopilot.log

set -euo pipefail
cd "$(dirname "$0")"

mkdir -p logs
LOG_FILE="logs/autopilot_$(date +%Y-%m-%d).log"

echo "--- Starting ArmApply Autopilot $(date) ---" | tee -a "$LOG_FILE"

# Load environment
if [[ -f .env.test ]]; then
  set -a
  source .env.test
  set +a
fi

# Run the autopilot script globally
export PYTHONPATH="."
python3 armapply/cron_autopilot.py | tee -a "$LOG_FILE"

echo "--- ArmApply Autopilot Finished $(date) ---" | tee -a "$LOG_FILE"
