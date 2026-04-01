import os
from pathlib import Path

# Repository root: armapply-backend/ (contains applypilot-src/ and armapply/)
ARMAPPLY_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("ARMAPPLY_DATA", ARMAPPLY_ROOT / "data")).resolve()
APP_DB_PATH = DATA_ROOT / "armapply.db"
JWT_SECRET = os.environ.get("ARMAPPLY_JWT_SECRET", "change-me-in-production-use-openssl-rand")
JWT_ALG = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ARMAPPLY_JWT_EXPIRE_MIN", "10080"))  # 7 days
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Pipeline lock: one ApplyPilot workspace active at a time per process (path patching).
PIPELINE_CONCURRENCY = int(os.environ.get("ARMAPPLY_PIPELINE_WORKERS", "1"))
