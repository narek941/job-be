"""ArmApply configuration — all settings from environment variables."""

import os
from pathlib import Path

# Repository root: armapply-backend/
ARMAPPLY_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("ARMAPPLY_DATA", ARMAPPLY_ROOT / "data")).resolve()

# JWT
JWT_SECRET = os.environ.get("ARMAPPLY_JWT_SECRET", "change-me-in-production-use-openssl-rand")
JWT_ALG = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ARMAPPLY_JWT_EXPIRE_MIN", "10080"))  # 7 days

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# LLM (detected by llm_client.py, but we expose env var names here for docs)
# GROQ_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, LLM_URL, LLM_MODEL

# Scheduler
SCHEDULER_INTERVAL_HOURS = int(os.environ.get("ARMAPPLY_SCHEDULE_HOURS", "8"))
