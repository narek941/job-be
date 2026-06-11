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
import re
from dataclasses import dataclass
from typing import Any, TypedDict

from jobfox import db, llm

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
    "You are a senior technical recruiter scoring how well a candidate fits "
    "a job listing. Output strict JSON with keys 'score' (int 1-10) and "
    "'reason' (one short sentence, max 200 chars).\n\n"
    "Scoring rules:\n"
    "  10 = excellent technical fit AND great location match.\n"
    "  8-9 = strong tech fit, location works (remote OR in/near candidate's home).\n"
    "  6-7 = decent tech fit but some friction (e.g. would require relocation, "
    "        seniority slightly off).\n"
    "  4-5 = partial overlap; many key skills missing or wrong domain.\n"
    "  1-3 = wrong field, wrong seniority, or job requires relocating away "
    "        from the candidate's preferred locations without being remote.\n\n"
    "Location is a HARD signal: a job in another country that isn't remote "
    "should never score above 6, no matter how strong the tech match. A job "
    "in the candidate's home country or fully remote keeps the location "
    "ceiling open.\n\n"
    "If the candidate states a DESIRED ROLE, a listing in a clearly "
    "different role family caps at 5 even with skill overlap. If the "
    "candidate states a MINIMUM SALARY and the listing's stated salary is "
    "clearly below it, cap at 5; if the listing doesn't state a salary, "
    "ignore the salary signal entirely."
)


def score_job(
    cv: str,
    job: db.Job,
    *,
    candidate_name: str | None = None,
    home_locations: list[str] | None = None,
    desired_role: str | None = None,
    salary_expectation: str | None = None,
) -> ScoreResult:
    """Returns a 1-10 fit score plus a short reason."""
    locations_line = ", ".join(home_locations) if home_locations else "Remote-friendly"
    name_line = f"Candidate's name: {candidate_name}\n" if candidate_name else ""
    role_line = f"Candidate's desired role: {desired_role}\n" if desired_role else ""
    salary_line = (
        f"Candidate's minimum salary expectation: {salary_expectation}\n"
        if salary_expectation
        else ""
    )
    user_prompt = (
        f"{name_line}{role_line}{salary_line}"
        f"Candidate's preferred locations: {locations_line}\n\n"
        f"CV:\n{_clip(cv, 8000)}\n\n---\n\nJob:\n{_job_brief(job)}"
    )
    data = llm.complete_json(
        system=_SCORE_SYSTEM,
        user=user_prompt,
        temperature=0.1,
        max_tokens=512,
    )
    if not isinstance(data, dict):
        raise llm.LLMError(f"score_job: expected object, got {type(data).__name__}")
    return ScoreResult(
        score=_clamp_score(data.get("score")),
        reason=str(data.get("reason") or "").strip()[:300],
    )


_COVER_SYSTEM = (
    "You are writing a cover letter FROM THE CANDIDATE'S OWN SIDE — "
    "first-person, formal, addressed to the recruiter. Target 150-210 "
    "words across 3-4 short paragraphs. Plain text only — no markdown, "
    "headers, labels, emojis, or bullet lists.\n\n"
    "VOICE — strict rules:\n"
    "  • Write entirely in first person: 'I', 'my', 'I built…'. NEVER "
    "    write about the candidate in third person. The opening sentence "
    "    must NOT start with the candidate's name or say '<Name> is "
    "    applying for…'. Open with 'I'm writing to apply for…' or "
    "    'I'm applying for…' or a similar first-person phrasing.\n"
    "  • The candidate's NAME is provided ONLY so you can avoid mistaking "
    "    it for a company. The name MUST NOT appear in the letter body — "
    "    the sign-off (added later by the caller) carries the name.\n\n"
    "GROUNDING — strict rules:\n"
    "  • Use ONLY employers, projects, and skills from the structured "
    "    profile. Do NOT invent companies, titles, dates, metrics, or "
    "    projects. If a metric isn't in the profile, don't invent one.\n"
    "  • The candidate's name and surname are NEVER company names. If a "
    "    profile entry's 'company' field looks like the candidate's own "
    "    name or surname (e.g. profile says company='Kolyan' and the "
    "    candidate is Narek Kolyan), treat that entry as freelance / "
    "    personal work — write 'on a freelance project' or 'in my own "
    "    practice', NEVER 'at Kolyan' or 'at <surname>'.\n"
    "  • Pick the 2-3 job requirements that have the strongest CV "
    "    evidence and ground each in a *specific* bullet or project — "
    "    name the technology, the system, or the outcome.\n"
    "  • If the job mentions a tech the candidate lacks, acknowledge "
    "    transferable adjacent experience honestly. Never fake the tech.\n"
    "  • No filler ('I am writing to express my interest…'), no "
    "    superlatives ('passionate', 'world-class', 'cutting-edge'), no "
    "    clichés, no asking-for-the-job platitudes.\n\n"
    "STRUCTURE:\n"
    "  1. Opening (1-2 sentences, first person): name the role + the "
    "     single strongest concrete reason I'm a fit. Lead with evidence, "
    "     not enthusiasm.\n"
    "  2. Middle (1-2 short paragraphs): tie 2-3 specific CV facts to "
    "     specific job requirements. Use the hiring company/product name "
    "     from the listing if it appears. Show, don't claim.\n"
    "  3. Close (1 sentence): a low-pressure invite to continue the "
    "     conversation. No 'thank you for your time' filler.\n\n"
    "Do NOT write a salutation ('Dear Hiring Manager' etc.) or sign-off "
    "block ('Best regards, …') — the caller adds those."
)


def _name_tokens(name: str | None) -> set[str]:
    """Lowercased word tokens from the candidate's name, length >= 3.

    Used to detect profile.experience entries whose `company` field is
    actually the candidate's own name (a common CV pattern for freelance
    or personal work). Two-letter tokens are dropped to avoid false hits
    on initials.
    """
    if not name:
        return set()
    return {t for t in re.findall(r"[A-Za-z]+", name.lower()) if len(t) >= 3}


def _sanitize_profile_for_cover(profile: dict | None, candidate_name: str | None) -> dict | None:
    """Relabel experience entries whose 'company' looks like the candidate's
    own name (case-insensitive token match). Prevents the LLM from writing
    'at <surname>'."""
    if not profile or not candidate_name:
        return profile
    tokens = _name_tokens(candidate_name)
    if not tokens:
        return profile
    exp = profile.get("experience") or []
    if not isinstance(exp, list):
        return profile
    rewritten = []
    changed = False
    for e in exp:
        if not isinstance(e, dict):
            rewritten.append(e)
            continue
        company = (e.get("company") or "").strip()
        company_tokens = _name_tokens(company)
        if company_tokens and company_tokens.issubset(tokens):
            rewritten.append({**e, "company": "Freelance / personal practice"})
            changed = True
        else:
            rewritten.append(e)
    if not changed:
        return profile
    return {**profile, "experience": rewritten}


def _profile_section(profile: dict | None) -> str:
    if not profile:
        return ""
    out: list[str] = ["STRUCTURED PROFILE (use as ground truth):"]
    if profile.get("headline"):
        out.append(f"Headline: {profile['headline']}")
    if profile.get("summary"):
        out.append(f"Summary: {profile['summary']}")
    if profile.get("skills"):
        out.append(f"Skills: {', '.join(profile['skills'][:25])}")
    exp = profile.get("experience") or []
    if exp:
        out.append("Experience (most recent first):")
        for e in exp[:5]:
            role = (e.get("role") or "").strip()
            company = (e.get("company") or "").strip()
            dates = f"{e.get('from', '')} – {e.get('to', '')}".strip(" –")
            out.append(f"  • {role} @ {company} ({dates})".rstrip(" ()"))
            for b in (e.get("bullets") or [])[:4]:
                if isinstance(b, str) and b.strip():
                    out.append(f"      - {b.strip()}")
    prj = profile.get("projects") or []
    if prj:
        out.append("Projects:")
        for p in prj[:5]:
            name = (p.get("name") or "").strip()
            stack = p.get("stack") or []
            desc = (p.get("desc") or "").strip()
            line = f"  • {name}"
            if isinstance(stack, list) and stack:
                line += f" [{', '.join(stack[:6])}]"
            if desc:
                line += f" — {desc}"
            out.append(line)
    return "\n".join(out) + "\n\n"


def cover_letter(
    cv: str,
    job: db.Job,
    *,
    candidate_name: str | None = None,
    profile: dict | None = None,
) -> str:
    """Returns a cover-letter body suitable for email."""
    safe_profile = _sanitize_profile_for_cover(profile, candidate_name)
    name_line = f"Candidate's name (for reference only — never write it in the letter): {candidate_name}\n\n" if candidate_name else ""
    profile_block = _profile_section(safe_profile)
    cv_block = f"RAW CV (fallback context only):\n{_clip(cv, 4000)}\n\n" if cv else ""
    text = llm.complete(
        system=_COVER_SYSTEM,
        user=f"{name_line}{profile_block}{cv_block}---\n\nJob:\n{_job_brief(job)}",
        temperature=0.4,
        max_tokens=1500,
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


def cv_tweaks(cv: str, job: db.Job, *, candidate_name: str | None = None) -> CvTweaks:
    """Returns minimal CV edits — bullets to add + optional summary rewrite."""
    name_line = f"Candidate's name: {candidate_name}\n\n" if candidate_name else ""
    data = llm.complete_json(
        system=_TWEAKS_SYSTEM,
        user=f"{name_line}CV:\n{_clip(cv, 8000)}\n\n---\n\nJob:\n{_job_brief(job)}",
        temperature=0.3,
        max_tokens=1024,
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
