"""Structured CV profile.

Parses raw CV text into a typed JSON object so:
  - the user can see *what the LLM understood* and correct it
  - cover-letter generation can reference specific bullets instead of
    re-reading the entire blob every time

`extract_profile()` is the only LLM call here. Editing helpers are pure
data transforms — they don't talk to Gemini.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from armapply import llm

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

class ExperienceEntry(TypedDict, total=False):
    company: str
    role: str
    from_: str   # serialized as 'from' in JSON (handled below)
    to: str
    bullets: list[str]


class ProjectEntry(TypedDict, total=False):
    name: str
    stack: list[str]
    desc: str


class EducationEntry(TypedDict, total=False):
    school: str
    degree: str
    year: str


class Profile(TypedDict, total=False):
    headline: str
    summary: str
    skills: list[str]
    experience: list[dict[str, Any]]
    projects: list[dict[str, Any]]
    education: list[dict[str, Any]]
    languages: list[str]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = (
    "You are a CV parser. Given the raw text of a candidate's résumé, "
    "extract a structured profile. Be conservative: copy phrasing from "
    "the source where possible, don't invent facts.\n\n"
    "Output strict JSON with this shape (omit any field that's truly "
    "absent — do not invent placeholders):\n"
    "{\n"
    '  "headline": "one-line role + level, e.g. Senior Frontend Engineer",\n'
    '  "summary": "2-3 sentence profile/about paragraph from the CV",\n'
    '  "skills": ["React", "TypeScript", ...],   // technical skills only, deduped\n'
    '  "experience": [\n'
    '    {"company": "Acme", "role": "Senior Engineer", "from": "Jan 2022", \n'
    '     "to": "Present", "bullets": ["…", "…"]}\n'
    "  ],\n"
    '  "projects": [{"name": "…", "stack": ["…"], "desc": "1 sentence"}],\n'
    '  "education": [{"school": "…", "degree": "…", "year": "2020"}],\n'
    '  "languages": ["English (fluent)", "Russian (native)"]\n'
    "}\n\n"
    "Rules:\n"
    "  - Up to 10 skills, most relevant first.\n"
    "  - Up to 8 experience entries, newest first; up to 5 bullets each.\n"
    "  - Up to 6 projects.\n"
    "  - bullets must be ≤180 chars, no trailing period required.\n"
    "  - If something is genuinely missing in the CV, omit that array — "
    "    do NOT return an empty placeholder."
)


def extract_profile(cv_text: str) -> Profile:
    """LLM-extract a structured profile from raw CV text. Raises LLMError
    on transport/parse failure."""
    if not cv_text or not cv_text.strip():
        return Profile()
    data = llm.complete_json(
        system=_EXTRACT_SYSTEM,
        user=cv_text[:12000],   # well under model context; we don't need more
        temperature=0.1,
        max_tokens=2048,
    )
    if not isinstance(data, dict):
        raise llm.LLMError(f"extract_profile: expected object, got {type(data).__name__}")
    return _sanitize(data)


def _sanitize(raw: dict[str, Any]) -> Profile:
    """Trim and normalize whatever the LLM gave us into our Profile shape."""
    p: Profile = {}
    if isinstance(raw.get("headline"), str):
        p["headline"] = raw["headline"].strip()[:200]
    if isinstance(raw.get("summary"), str):
        p["summary"] = raw["summary"].strip()[:1500]
    if isinstance(raw.get("skills"), list):
        seen: set[str] = set()
        skills: list[str] = []
        for s in raw["skills"]:
            if not isinstance(s, str):
                continue
            t = s.strip()
            key = t.lower()
            if t and key not in seen:
                seen.add(key)
                skills.append(t[:60])
            if len(skills) >= 30:
                break
        if skills:
            p["skills"] = skills
    for plural, max_items in (("experience", 10), ("projects", 8), ("education", 6)):
        v = raw.get(plural)
        if isinstance(v, list):
            cleaned = [e for e in v if isinstance(e, dict)][:max_items]
            if cleaned:
                p[plural] = cleaned  # type: ignore[literal-required]
    if isinstance(raw.get("languages"), list):
        langs = [
            l.strip()[:80]
            for l in raw["languages"]
            if isinstance(l, str) and l.strip()
        ][:10]
        if langs:
            p["languages"] = langs
    return p


# ---------------------------------------------------------------------------
# Pure editing helpers (no LLM)
# ---------------------------------------------------------------------------

def add_skills(profile: Profile, new_skills: list[str]) -> Profile:
    existing = list(profile.get("skills") or [])
    seen = {s.lower() for s in existing}
    for s in new_skills:
        s = s.strip()
        if s and s.lower() not in seen:
            existing.append(s[:60])
            seen.add(s.lower())
    return {**profile, "skills": existing[:30]}


def remove_skills(profile: Profile, removals: list[str]) -> Profile:
    drop = {s.strip().lower() for s in removals if s.strip()}
    existing = [s for s in (profile.get("skills") or []) if s.lower() not in drop]
    return {**profile, "skills": existing}


def set_summary(profile: Profile, summary: str) -> Profile:
    return {**profile, "summary": summary.strip()[:1500]}


# ---------------------------------------------------------------------------
# Rendering for Telegram (plain text — no Markdown, see bot.py)
# ---------------------------------------------------------------------------

def render(profile: Profile | None) -> str:
    if not profile:
        return "❌ No structured profile yet. Re-upload a PDF or run /profile rebuild."

    lines: list[str] = []
    if profile.get("headline"):
        lines.append(f"💼 {profile['headline']}")
    if profile.get("summary"):
        lines.append("")
        lines.append("📝 Summary")
        lines.append(profile["summary"])

    if profile.get("skills"):
        lines.append("")
        lines.append("🛠 Skills")
        lines.append(", ".join(profile["skills"]))

    exp = profile.get("experience") or []
    if exp:
        lines.append("")
        lines.append("📂 Experience")
        for e in exp[:5]:
            role = (e.get("role") or "—").strip()
            company = (e.get("company") or "—").strip()
            dates = f"{(e.get('from') or '').strip()} – {(e.get('to') or '').strip()}".strip(" –")
            lines.append(f"• {role} @ {company}" + (f" ({dates})" if dates else ""))
            for b in (e.get("bullets") or [])[:3]:
                if isinstance(b, str) and b.strip():
                    lines.append(f"   – {b.strip()}")

    prj = profile.get("projects") or []
    if prj:
        lines.append("")
        lines.append("🚀 Projects")
        for p in prj[:5]:
            name = (p.get("name") or "—").strip()
            stack = p.get("stack") or []
            stack_str = f" [{', '.join(stack)}]" if isinstance(stack, list) and stack else ""
            desc = (p.get("desc") or "").strip()
            lines.append(f"• {name}{stack_str}")
            if desc:
                lines.append(f"   {desc}")

    edu = profile.get("education") or []
    if edu:
        lines.append("")
        lines.append("🎓 Education")
        for e in edu[:3]:
            school = (e.get("school") or "—").strip()
            degree = (e.get("degree") or "").strip()
            year = (e.get("year") or "").strip()
            lines.append(f"• {school}" + (f" — {degree}" if degree else "") + (f" ({year})" if year else ""))

    langs = profile.get("languages") or []
    if langs:
        lines.append("")
        lines.append("🗣 Languages: " + ", ".join(langs))

    return "\n".join(lines)
