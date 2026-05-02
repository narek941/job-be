"""LLM features: interview prep, recruiter reply drafts, cover letters, profile extraction.

Uses armapply.llm_client instead of applypilot.llm.
"""

from __future__ import annotations

from armapply.llm_client import get_client


def generate_interview_prep(job: dict, language: str = "ru") -> str:
    """Generate interview preparation materials for a job."""
    jd = (job.get("full_description") or job.get("description") or "")[:8000]
    title = job.get("title") or ""
    company = job.get("site") or ""

    lang_note = (
        "Ответ на русском, структурированный Markdown с заголовками ##."
        if language.startswith("ru")
        else "Answer in English, Markdown with ## headings."
    )

    prompt = f"""You are a career coach. {lang_note}

Target role title (from listing): {title}
Company/source label: {company}

Job description:
{jd}

Produce:
## Кратко о роли / Short role summary
## Что выделить из опыта кандидата / Experience angles
## Вероятные технические вопросы / Likely technical questions (5–8)
## Поведенческие (STAR) / Behavioral questions (3–5)
## Вопросы кандидата работодателю / Questions to ask the employer (5)
## Чек-лист на день интервью / Day-of checklist

Stay factual; do not invent employer details not in the text."""

    client = get_client()
    return client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.4,
    )


def generate_recruiter_reply_draft(
    job: dict,
    recruiter_message: str,
    language: str = "ru",
    tone: str = "professional_warm",
) -> str:
    """Generate a draft reply to a recruiter message."""
    jd = (job.get("full_description") or "")[:4000]
    title = job.get("title") or ""

    lang = "Russian" if language.startswith("ru") else "English"
    prompt = f"""Write a SHORT reply email/message from the candidate to a recruiter or employer.
Language: {lang}
Tone: {tone} (direct, no fluff, no clichés like "I hope this email finds you well").

Role discussed: {title}

Job context (excerpt):
{jd}

Recruiter's last message (quote or paraphrase):
{recruiter_message[:4000]}

Rules:
- Do not fabricate facts; if scheduling, say you will propose 2–3 time windows in a follow-up.
- Output ONLY the message body text, no subject line unless clearly needed as one line at top.
- Keep under 180 words unless the recruiter asked multiple questions."""

    client = get_client()
    return client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=1024,
        temperature=0.5,
    )


def _get_user_resume_text(user_id: int | None) -> str:
    """Load the user's resume from DB; fall back to cv_template if none."""
    if user_id is not None:
        from armapply.users_db import get_user_resume
        resume = get_user_resume(user_id)
        if resume and len(resume.strip()) > 50:
            return resume
    # Fallback: hardcoded CV template
    from armapply.cv_template import render_cv_text
    return render_cv_text()


def generate_tailored_cover_letter(job: dict, language: str = "en", user_id: int | None = None) -> str:
    """Generates a warm, human-sounding cover letter based on the user's resume.

    If user_id is provided, loads their resume from the database.
    Falls back to the default CV template if no resume is found.
    """
    resume_text = _get_user_resume_text(user_id)

    jd    = (job.get("full_description") or job.get("description") or "")[:5000]
    title = job.get("title") or "this role"
    company = job.get("site") or "your company"

    lang_note = "Write in Armenian (Eastern Armenian, professional tone)." if language.startswith("hy") else "Write in English."

    prompt = f"""You are writing a cover letter for the candidate below (first person, "I").
{lang_note}

TONE RULES — extremely important:
- Sound like a real human being, NOT a corporate robot. Keep it conversational but professional.
- No clichés: avoid "I am excited to apply", "I hope this email finds you well", 
  "I am passionate about growing", "synergize", "leverage", "cutting-edge".
- Make it hyper-specific to the job requirements. Show, don't just tell, that you have the skills they need by citing your exact past work.
- Be direct and warm. 180–220 words max. Do not use bullet points.
- FIRST sentence must hook immediately — who you are + one concrete achievement relevant to the role.

CANDIDATE RESUME:
{resume_text[:6000]}

ROLE APPLYING TO: {title} at {company}

JOB DESCRIPTION (excerpt):
{jd}

STRUCTURE (follow exactly):
1. Opening (1 sentence) — who the candidate is + one concrete number/achievement that is relevant to this JD.
2. Body paragraph 1 — most relevant past project or role (be specific: tech stack, scale, outcome).
3. Body paragraph 2 — what appeals to the candidate about this specific role/company (reference something real from the JD).
4. Closing (2 sentences) — express readiness to talk, no fluff.

Output ONLY the letter body (no subject line, no signature block)."""

    client = get_client()
    return client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=1024,
        temperature=0.7,
    )

def generate_tailored_resume_text(job: dict, user_id: int | None = None) -> str:
    """
    Returns the user's resume text.
    If user has a resume in DB, returns that. Otherwise falls back to cv_template.
    """
    return _get_user_resume_text(user_id)

def extract_profile_from_resume(resume_text: str) -> dict:
    """Uses LLM to extract structured data from a raw resume text."""
    prompt = f"""
    You are a world-class IT Recruiter and CV Parser. 
    Analyze the following resume text and extract structured information into the exact JSON format below.
    
    JSON Schema:
    {{
      "personal": {{
        "full_name": "string",
        "role_title": "string (e.g., 'Senior DevOps Engineer')",
        "email": "string",
        "phone": "string",
        "bio": "string (Professional Summary)",
        "skills": "string (comma-separated techs, e.g., 'React, CI/CD')",
        "work_experience_text": "string",
        "education_text": "string",
        "links_text": "string",
        "github_url": "string",
        "linkedin_url": "string",
        "languages": "string (e.g., 'Armenian (Native)')"
      }}
    }}
    
    CRITICAL RULES:
    1. EXTRACT facts exactly; do not hallucinate metrics.
    2. If a field is not found, use an empty string "".
    3. Return ONLY a valid JSON object. No preamble, no commentary.
    
    Resume Text:
    {resume_text[:10000]}
    """
    
    import logging
    log = logging.getLogger(__name__)
    
    client = get_client()
    try:
        response = client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.3,
        )
    except Exception as e:
        log.error(f"LLM API call failed in extract_profile_from_resume: {e}")
        return {"personal": {}}
    
    try:
        import json
        import re
        # Try to find JSON block if the model added text
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(response)
    except Exception as e:
        log.error(f"JSON parsing failed in extract_profile_from_resume: {e}. Output was: {response[:200]}")
        return {"personal": {}}
