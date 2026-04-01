"""Call ApplyPilot stages inside an activated user workspace."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from applypilot.database import get_connection
from applypilot.pipeline import _run_discover, _run_enrich, _run_score
from applypilot.scoring.cover_letter import generate_cover_letter
from applypilot.scoring.scorer import score_job
from applypilot.scoring.tailor import tailor_resume

log = logging.getLogger(__name__)


def discover_and_enrich(user_id: int, workers: int = 1) -> dict:
    from armapply.users_db import upsert_jobs_batch
    d = _run_discover(workers=workers)
    e = _run_enrich(workers=workers)
    
    # Sync result back to Supabase
    conn = get_connection()
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    jobs_list = [dict(r) for r in rows]
    new_c, up_c = upsert_jobs_batch(user_id, jobs_list, "Discovery", "sync")
    
    return {"discover": d, "enrich": e, "supabase_sync": {"new": new_c, "updated": up_c}}


def score_jobs_batch(user_id: int, limit: int = 0) -> dict:
    from applypilot.pipeline import _run_score
    from armapply.users_db import upsert_jobs_batch
    res = _run_score(limit=limit, rescore=False)
    
    # Sync result back to Supabase (scores)
    conn = get_connection()
    rows = conn.execute("SELECT * FROM jobs WHERE fit_score IS NOT NULL").fetchall()
    jobs_list = [dict(r) for r in rows]
    upsert_jobs_batch(user_id, jobs_list, "Scoring", "sync")
    
    return res


def _job_row(user_id: int, url: str) -> dict | None:
    from armapply.users_db import get_job_by_url
    return get_job_by_url(user_id, url)


def tailor_job_by_url(user_id: int, url: str, min_score: int, validation_mode: str) -> dict:
    from applypilot.config import TAILORED_DIR, load_profile, RESUME_PATH

    job = _job_row(user_id, url)
    if not job:
        return {"ok": False, "error": "job_not_found"}
    if job.get("fit_score") is None:
        return {"ok": False, "error": "not_scored"}
    if job["fit_score"] < min_score:
        return {"ok": False, "error": "score_below_min", "fit_score": job["fit_score"]}
    if not job.get("full_description"):
        return {"ok": False, "error": "not_enriched"}

    profile = load_profile()
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    tailored, report = tailor_resume(resume_text, job, profile, validation_mode=validation_mode)

    safe_title = re.sub(r"[^\w\s-]", "", job["title"] or "")[:50].strip().replace(" ", "_")
    safe_site = re.sub(r"[^\w\s-]", "", job["site"] or "")[:20].strip().replace(" ", "_")
    prefix = f"{safe_site}_{safe_title}"
    TAILORED_DIR.mkdir(parents=True, exist_ok=True)
    txt_path = TAILORED_DIR / f"{prefix}.txt"
    txt_path.write_text(tailored, encoding="utf-8")

    pdf_path = None
    if report["status"] in ("approved", "approved_with_judge_warning"):
        try:
            from applypilot.scoring.pdf import convert_to_pdf

            pdf_path = str(convert_to_pdf(txt_path))
        except Exception:
            log.debug("pdf fail", exc_info=True)

    from armapply.users_db import update_job_field
    now = datetime.now(timezone.utc).isoformat()
    ok_status = report["status"] in ("approved", "approved_with_judge_warning")
    if ok_status:
        update_job_field(user_id, url, "tailored_resume_path", str(txt_path))
        update_job_field(user_id, url, "tailored_at", now)
        update_job_field(user_id, url, "tailor_attempts", (job.get("tailor_attempts") or 0) + 1)
    else:
        update_job_field(user_id, url, "tailor_attempts", (job.get("tailor_attempts") or 0) + 1)

    return {
        "ok": ok_status,
        "status": report["status"],
        "tailored_resume_path": str(txt_path) if ok_status else None,
        "pdf_path": pdf_path,
        "report": report,
    }


def cover_letter_for_job(user_id: int, url: str, validation_mode: str) -> dict:
    from applypilot.config import COVER_LETTER_DIR, load_profile, RESUME_PATH

    job = _job_row(user_id, url)
    if not job:
        return {"ok": False, "error": "job_not_found"}
    if not job.get("tailored_resume_path"):
        return {"ok": False, "error": "not_tailored"}
    profile = load_profile()
    resume_path = Path(job["tailored_resume_path"])
    resume_text = resume_path.read_text(encoding="utf-8") if resume_path.exists() else RESUME_PATH.read_text(
        encoding="utf-8"
    )

    letter = generate_cover_letter(resume_text, job, profile, validation_mode=validation_mode)
    safe_title = re.sub(r"[^\w\s-]", "", job["title"] or "")[:50].strip().replace(" ", "_")
    safe_site = re.sub(r"[^\w\s-]", "", job["site"] or "")[:20].strip().replace(" ", "_")
    prefix = f"{safe_site}_{safe_title}"
    COVER_LETTER_DIR.mkdir(parents=True, exist_ok=True)
    cl_path = COVER_LETTER_DIR / f"{prefix}_CL.txt"
    cl_path.write_text(letter, encoding="utf-8")

    pdf_path = None
    try:
        from applypilot.scoring.pdf import convert_to_pdf

        pdf_path = str(convert_to_pdf(cl_path))
    except Exception:
        pass

    from armapply.users_db import update_job_field
    now = datetime.now(timezone.utc).isoformat()
    update_job_field(user_id, url, "cover_letter_path", str(cl_path))
    update_job_field(user_id, url, "cover_letter_at", now)
    update_job_field(user_id, url, "cover_attempts", (job.get("cover_attempts") or 0) + 1)

    return {"ok": True, "cover_letter_path": str(cl_path), "pdf_path": pdf_path}


def score_one_job(user_id: int, url: str) -> dict:
    from applypilot.config import RESUME_PATH

    job = _job_row(user_id, url)
    if not job:
        return {"ok": False, "error": "job_not_found"}
    if not job.get("full_description"):
        return {"ok": False, "error": "not_enriched"}

    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    result = score_job(resume_text, job)
    from armapply.users_db import update_job_field
    now = datetime.now(timezone.utc).isoformat()
    update_job_field(user_id, url, "fit_score", result["score"])
    update_job_field(user_id, url, "score_reasoning", f"{result['keywords']}\n{result['reasoning']}")
    update_job_field(user_id, url, "scored_at", now)
    return {"ok": True, "score": result["score"], "reasoning": result["reasoning"], "keywords": result["keywords"]}


def run_auto_apply(
    job_url: str | None,
    min_score: int,
    dry_run: bool,
    headless: bool,
    model: str = "haiku",
) -> None:
    from applypilot.apply.launcher import main as apply_main

    apply_main(
        limit=1,
        target_url=job_url,
        min_score=min_score,
        headless=headless,
        model=model,
        dry_run=dry_run,
        continuous=False,
        workers=1,
    )


def save_uploaded_resume_pdf(user_root: Path, data: bytes) -> None:
    pdf_path = user_root / "resume.pdf"
    txt_path = user_root / "resume.txt"
    pdf_path.write_bytes(data)
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(str(pdf_path))
        parts: list[str] = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        txt_path.write_text("\n\n".join(parts), encoding="utf-8")
    except Exception:
        txt_path.write_text("[PDF uploaded — text extraction failed; replace resume.txt manually]\n", encoding="utf-8")


def save_uploaded_resume_text(user_root: Path, text: str) -> None:
    (user_root / "resume.txt").write_text(text, encoding="utf-8")
