#!/usr/bin/env bash
# Run the API with test environment variables (can override from environment).
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .env.test ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.test
  set +a
fi
export JOBFOX_JWT_SECRET="${JOBFOX_JWT_SECRET:-jobfox-test-jwt-secret-change-in-prod}"
ROOT="$(pwd)"
export JOBFOX_DATA="${JOBFOX_DATA:-$ROOT/data}"
mkdir -p "$JOBFOX_DATA"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
PY="${ROOT}/.venv/bin/python3"
"$PY" -m pip install -q -r requirements.txt
export PYTHONPATH="$ROOT"
exec "$PY" -m uvicorn jobfox.main:app --host 0.0.0.0 --port 8000 --reload
