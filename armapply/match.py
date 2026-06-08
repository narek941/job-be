"""Match jobs against a user's CV.

Three independent operations, each a single LLM call:
  * `score_job`        -> int 1..10 + reason
  * `cover_letter`     -> string body
  * `cv_tweaks`        -> structured edits (bullets to add, optional rewrite)

We never rewrite the whole CV — the goal is *minimal* changes that nudge a
human-readable resume toward the listing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from armapply import db, llm

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

class CvTweaks(TypedDict):
    bullets_to_add: list[str]
    summary_rewrite: str | None


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: int
    reason: str


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _job_brief(job: db.Job) -> str:
    parts = [
        f"Title: {job['title']}",
        f"Company: {job['company'] or 'Unknown'}",
        f"Location: {job['location'] or '—'}",
    ]
    if job["salary"]:
        parts.append(f"Salary: {job['salary']}")
    desc = (job["description"] or "").strip()
    if len(desc) > 6000:
        desc = desc[:6000] + "\n… (truncated)"
    parts.append("Description:\n" + desc)
    return "\n".join(parts)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


def _clamp_score(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(1, min(10, n))


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

_SCORE_SYSTEM = (
    "You are a senior technical recruiter. Score how well the candidate's CV "
    "matches the job listing on a scale 1-10. 10 = excellent fit; 1 = wrong field. "
    "Penalize mismatched seniority, missing required tech, wrong domain. "
    "Be honest — most jobs are 4-7. Output strict JSON with keys 'score' (int 1-10) "
    "and 'reason' (one short sentence, max 200 chars)."
)


def score_job(cv: str, job: db.Job) -> ScoreResult:
    """Returns a 1-10 fit score plus a short reason."""
    data = llm.complete_json(
        system=_SCORE_SYSTEM,
        user=f"CV:\n{_clip(cv, 8000)}\n\n---\n\nJob:\n{_job_brief(job)}",
        temperature=0.1,
        max_tokens=300,
    )
    if not isinstance(data, dict):
        raise llm.LLMError(f"score_job: expected object, got {type(data).__name__}")
    return ScoreResult(
        score=_clamp_score(data.get("score")),
        reason=str(data.get("reason") or "").strip()[:300],
    )


_COVER_SYSTEM = (
    "You are the candidate. Write a short, sincere cover letter (4-6 short "
    "paragraphs, 180-280 words total) for the role below. Anchor every claim "
    "in the CV — do not invent experience. No fluff, no hyperbole, no emojis. "
    "Start with the role and one specific reason it interests you. End with a "
    "single concrete call to action. Output plain text only."
)


def cover_letter(cv: str, job: db.Job) -> str:
    """Returns a cover-letter body suitable for email."""
    text = llm.complete(
        system=_COVER_SYSTEM,
        user=f"CV:\n{_clip(cv, 8000)}\n\n---\n\nJob:\n{_job_brief(job)}",
        temperature=0.4,
        max_tokens=700,
    )
    return text.strip()


_TWEAKS_SYSTEM = (
    "You are the candidate's CV editor. Suggest MINIMAL, truthful edits to "
    "nudge the CV toward the job. Output strict JSON with: "
    "'bullets_to_add' (array of up to 3 short bullet strings, each <140 chars, "
    "each MUST be supportable by the existing CV — do not fabricate); "
    "'summary_rewrite' (one rewritten 'summary' paragraph 2-3 sentences, or null "
    "if the existing summary is already a good fit). If no change is needed, "
    "return {\"bullets_to_add\": [], \"summary_rewrite\": null}."
)


def cv_tweaks(cv: str, job: db.Job) -> CvTweaks:
    """Returns minimal CV edits — bullets to add + optional summary rewrite."""
    data = llm.complete_json(
        system=_TWEAKS_SYSTEM,
        user=f"CV:\n{_clip(cv, 8000)}\n\n---\n\nJob:\n{_job_brief(job)}",
        temperature=0.3,
        max_tokens=600,
    )
    if not isinstance(data, dict):
        raise llm.LLMError(f"cv_tweaks: expected object, got {type(data).__name__}")
    bullets_raw = data.get("bullets_to_add") or []
    bullets = [
        str(b).strip()[:200]
        for b in bullets_raw
        if isinstance(b, str) and b.strip()
    ][:3]
    summary = data.get("summary_rewrite")
    summary = str(summary).strip()[:600] if isinstance(summary, str) and summary.strip() else None
    return CvTweaks(bullets_to_add=bullets, summary_rewrite=summary)


# ---------------------------------------------------------------------------
# CV text extraction from PDF (kept here so match.py owns "anything CV")
# ---------------------------------------------------------------------------

def extract_cv_text(pdf_bytes: bytes) -> str:
    """Best-effort PDF → text.

    Try pdfplumber first (fast, no extra memory). If it returns almost
    nothing (image-only/scanned PDF), fall back to OCR — but page-by-page
    at 100 DPI and capped at 5 pages so we don't OOM on a 512 MB worker.
    """
    try:
        import io
        import pdfplumber

        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
        text = "\n\n".join(parts).strip()
    except Exception as e:
        log.warning("pdfplumber extraction failed: %s", e)
        text = ""

    # Skip OCR if pdfplumber already got something useful — OCR is expensive
    # and only needed for image-only PDFs.
    if len(text) >= 200:
        return text

    try:
        import pdf2image  # type: ignore[import-not-found]
        import pytesseract  # type: ignore[import-not-found]
    except ImportError:
        log.info("OCR deps not installed; returning pdfplumber output")
        return text

    ocr_parts: list[str] = []
    try:
        # Probe page count cheaply via pdfinfo (poppler), capped to 5 pages.
        info = pdf2image.pdfinfo_from_bytes(pdf_bytes)
        total_pages = min(int(info.get("Pages", 1)), 5)
        for page_num in range(1, total_pages + 1):
            images = pdf2image.convert_from_bytes(
                pdf_bytes,
                dpi=100,
                first_page=page_num,
                last_page=page_num,
                fmt="jpeg",
            )
            if not images:
                continue
            ocr_parts.append(pytesseract.image_to_string(images[0]))
            # Release the PIL image before the next iteration so RSS stays flat.
            images[0].close()
    except Exception as e:
        log.warning("OCR fallback failed: %s", e)

    ocr_text = "\n\n".join(p for p in ocr_parts if p).strip()
    return ocr_text if len(ocr_text) > len(text) else text
