#!/usr/bin/env bash
# Daily Cron script wrapper for AutoPilot Bot
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .env.test ]]; then
  set -a
  source .env.test
  set +a
fi
export ARMAPPLY_JWT_SECRET="${ARMAPPLY_JWT_SECRET:-armapply-test-jwt-secret-change-in-prod}"
ROOT="$(pwd)"
export ARMAPPLY_DATA="${ARMAPPLY_DATA:-$ROOT/data}"
if [[ ! -d .venv ]]; then
  echo "Virtual environment not found, run run-dev.sh first"
  exit 1
fi
source .venv/bin/activate
export PYTHONPATH="$ROOT"
PY="${ROOT}/.venv/bin/python3"

echo "[$(date)] Starting daily bot..."
"$PY" -m armapply.daily_bot "$@"
echo "[$(date)] Daily bot finished."
