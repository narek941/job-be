"""Point ApplyPilot at a per-user directory."""

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import applypilot.config as ap_cfg
import applypilot.database as ap_db

_pipeline_lock = Lock()


def user_applypilot_dir(user_id: int) -> Path:
    from armapply.config import DATA_ROOT

    return (DATA_ROOT / "users" / str(user_id)).resolve()


def ensure_user_workspace(user_id: int) -> Path:
    from armapply.config import ARMAPPLY_ROOT

    root = user_applypilot_dir(user_id)
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("tailored_resumes", "cover_letters", "logs", "chrome-workers", "apply-workers"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    profile = root / "profile.json"
    if not profile.exists():
        example = ARMAPPLY_ROOT / "applypilot-src" / "profile.example.json"
        if example.exists():
            shutil.copy(example, profile)
        else:
            profile.write_text("{}", encoding="utf-8")

    resume = root / "resume.txt"
    if not resume.exists():
        resume.write_text(
            "Paste your resume here (plain text). Upload via API replaces this file.\n",
            encoding="utf-8",
        )

    searches = root / "searches.yaml"
    if not searches.exists():
        default = ARMAPPLY_ROOT / "armapply" / "defaults" / "searches.armenia.yaml"
        if default.exists():
            shutil.copy(default, searches)

    env_file = root / ".env"
    if not env_file.exists():
        env_example = ARMAPPLY_ROOT / "applypilot-src" / ".env.example"
        if env_example.exists():
            shutil.copy(env_example, env_file)

    return root


def _patch_applypilot_paths(root: Path) -> None:
    root = root.resolve()
    ap_cfg.APP_DIR = root
    ap_cfg.DB_PATH = root / "applypilot.db"
    ap_cfg.PROFILE_PATH = root / "profile.json"
    ap_cfg.RESUME_PATH = root / "resume.txt"
    ap_cfg.RESUME_PDF_PATH = root / "resume.pdf"
    ap_cfg.SEARCH_CONFIG_PATH = root / "searches.yaml"
    ap_cfg.ENV_PATH = root / ".env"
    ap_cfg.TAILORED_DIR = root / "tailored_resumes"
    ap_cfg.COVER_LETTER_DIR = root / "cover_letters"
    ap_cfg.LOG_DIR = root / "logs"
    ap_cfg.CHROME_WORKER_DIR = root / "chrome-workers"
    ap_cfg.APPLY_WORKER_DIR = root / "apply-workers"

    ap_db.DB_PATH = ap_cfg.DB_PATH

    import applypilot.view as ap_view

    ap_view.APP_DIR = ap_cfg.APP_DIR
    ap_view.DB_PATH = ap_cfg.DB_PATH

    import applypilot.enrichment.detail as ap_detail

    ap_detail.DB_PATH = ap_cfg.DB_PATH

    import applypilot.scoring.tailor as ap_tailor

    ap_tailor.RESUME_PATH = ap_cfg.RESUME_PATH
    ap_tailor.TAILORED_DIR = ap_cfg.TAILORED_DIR

    import applypilot.scoring.scorer as ap_scorer

    ap_scorer.RESUME_PATH = ap_cfg.RESUME_PATH

    import applypilot.scoring.cover_letter as ap_cover

    ap_cover.RESUME_PATH = ap_cfg.RESUME_PATH
    ap_cover.COVER_LETTER_DIR = ap_cfg.COVER_LETTER_DIR

    import applypilot.scoring.pdf as ap_pdf

    ap_pdf.TAILORED_DIR = ap_cfg.TAILORED_DIR


def activate_user_workspace(user_id: int) -> Path:
    root = ensure_user_workspace(user_id)
    _patch_applypilot_paths(root)
    ap_cfg.load_env()
    ap_cfg.ensure_dirs()
    ap_db.init_db()
    return root


def merge_profile_armapply(user_id: int, prefs: dict) -> None:
    root = ensure_user_workspace(user_id)
    profile_path = root / "profile.json"
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    arm = data.get("armapply") or {}
    arm.update(prefs)
    data["armapply"] = arm
    profile_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@contextmanager
def pipeline_context(user_id: int):
    with _pipeline_lock:
        activate_user_workspace(user_id)
        yield


async def run_in_pipeline(user_id: int, fn, *args, **kwargs):
    def _work():
        with _pipeline_lock:
            activate_user_workspace(user_id)
            return fn(*args, **kwargs)

    return await asyncio.to_thread(_work)
