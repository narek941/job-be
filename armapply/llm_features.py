"""LLM: подготовка к интервью и черновик ответа рекрутеру (без автоотправки)."""

from __future__ import annotations

from applypilot.config import load_profile
from applypilot.llm import get_client


def generate_interview_prep(job: dict, language: str = "ru") -> str:
    profile = load_profile()
    name = (profile.get("personal") or {}).get("preferred_name") or (profile.get("personal") or {}).get(
        "full_name", "Candidate"
    )
    jd = (job.get("full_description") or job.get("description") or "")[:8000]
    title = job.get("title") or ""
    company = job.get("site") or ""

    lang_note = (
        "Ответ на русском, структурированный Markdown с заголовками ##."
        if language.startswith("ru")
        else "Answer in English, Markdown with ## headings."
    )

    prompt = f"""You are a career coach. {lang_note}

Candidate: {name}
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
    profile = load_profile()
    personal = profile.get("personal") or {}
    name = personal.get("preferred_name") or personal.get("full_name", "")
    jd = (job.get("full_description") or "")[:4000]
    title = job.get("title") or ""

    lang = "Russian" if language.startswith("ru") else "English"
    prompt = f"""Write a SHORT reply email/message from the candidate to a recruiter or employer.
Language: {lang}
Tone: {tone} (direct, no fluff, no clichés like "I hope this email finds you well").

Candidate name: {name}
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


def generate_tailored_cover_letter(job: dict, language: str = "en") -> str:
    """Generates a warm, human-sounding cover letter based on Narek's real CV."""
    from armapply.cv_template import (
        NAME, EMAIL, PHONE, SUMMARY, SKILLS, EXPERIENCE, PROJECTS
    )
    
    jd    = (job.get("full_description") or job.get("description") or "")[:5000]
    title = job.get("title") or "this role"
    company = job.get("site") or "your company"

    # Build experience bullet summary for the prompt
    exp_text = "\n".join(
        f"- {e['role']} at {e['company']} ({e['period']}): " + "; ".join(e["bullets"][:3])
        for e in EXPERIENCE
    )
    project_text = "\n".join(
        f"- {p[0]}: {p[1]}.  {p[2]}"
        for p in PROJECTS[:4]
    )
    skills_flat = ", ".join(
        s for items in SKILLS.values() for s in items[:3]
    )

    lang_note = "Write in Armenian (Eastern Armenian, professional tone)." if language.startswith("hy") else "Write in English."

    prompt = f"""You are writing a cover letter AS Narek Kolyan (first person, "I").
{lang_note}

TONE RULES — extremely important:
- Sound like a real human being, NOT a corporate robot. Keep it conversational but professional.
- No clichés: avoid "I am excited to apply", "I hope this email finds you well", 
  "I am passionate about growing", "synergize", "leverage", "cutting-edge".
- Make it hyper-specific to the job requirements. Show, don't just tell, that you have the skills they need by citing your exact past work.
- Be direct and warm. 180–220 words max. Do not use bullet points.
- FIRST sentence must hook immediately — who you are + one concrete achievement relevant to the role.

CANDIDATE PROFILE:
  Name: {NAME}
  Phone: {PHONE}  Email: {EMAIL}
  Summary: {SUMMARY}
  Core skills: {skills_flat}

EXPERIENCE:
{exp_text}

SELECTED PROJECTS:
{project_text}

ROLE APPLYING TO: {title} at {company}

JOB DESCRIPTION (excerpt):
{jd}

STRUCTURE (follow exactly):
1. Opening (1 sentence) — who Narek is + one concrete number/achievement that is relevant to this JD.
2. Body paragraph 1 — most relevant past project or role (be specific: tech stack, scale, outcome).
3. Body paragraph 2 — what appeals to him about this specific role/company (reference something real from the JD).
4. Closing (2 sentences) — express readiness to talk, no fluff.

Output ONLY the letter body (no subject line, no signature block)."""

    client = get_client()
    return client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=1024,
        temperature=0.7,
    )

def generate_tailored_resume_text(job: dict) -> str:
    """
    Returns the standard plain-text CV version.
    CV tailoring is disabled by user request to always use the same CV.
    """
    from armapply.cv_template import render_cv_text
    
    # We no longer use an LLM or highlight specific skills.
    # Return the static CV identically every time.
    return render_cv_text()

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
