#!/usr/bin/env bash
# Запуск API с тестовыми переменными (можно переопределить из окружения).
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .env.test ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.test
  set +a
fi
export ARMAPPLY_JWT_SECRET="${ARMAPPLY_JWT_SECRET:-armapply-test-jwt-secret-change-in-prod}"
ROOT="$(pwd)"
export ARMAPPLY_DATA="${ARMAPPLY_DATA:-$ROOT/data}"
mkdir -p "$ARMAPPLY_DATA"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
PY="${ROOT}/.venv/bin/python3"
"$PY" -m pip install -q -e ./applypilot-src
"$PY" -m pip install -q -r requirements.txt
"$PY" -m playwright install chromium
export PYTHONPATH="$ROOT"
exec "$PY" -m uvicorn armapply.main:app --host 0.0.0.0 --port 8000 --reload
