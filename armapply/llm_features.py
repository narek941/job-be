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
    """Generates a cover letter focused on TECHNICAL SKILLS alignment."""
    profile = load_profile()
    personal = profile.get("personal") or {}
    name = personal.get("full_name", "Candidate")
    role = personal.get("role_title", "Engineer")
    skills = personal.get("skills", "")
    experience = personal.get("work_experience_text", "")
    
    jd = (job.get("full_description") or job.get("description") or "")[:5000]
    title = job.get("title") or ""
    company = job.get("site") or "the company"

    lang_note = "Russian" if language.startswith("ru") else "English"
    prompt = f"""You are a top-tier technical candidate. Write a highly technical, concise cover letter for the '{title}' role at '{company}'.
    Language: {lang_note}
    
    FOCUS: 
    - Lead with your technical stack and seniority.
    - Highlight specific technical achievements from your experience that map directly to the job's technical requirements.
    - Discuss architecture, scalability, or specific tools (e.g. React, Node, AWS) rather than soft skills.
    - Keep it under 250 words. No "fluff" or generic praise of the company.
    
    Candidate Identity: {name}, {role}
    Candidate Skills: {skills}
    Candidate Experience: {experience}
    
    Job Description:
    {jd}
    
    Structure:
    1. Hook: 1 sentence on why your specific technical background is a 10/10 fit.
    2. Body: 2 paragraphs detailing technical problems you solved that are relevant to this JD.
    3. Closing: Technical call to action (ready to discuss architecture/details).
    """

    client = get_client()
    return client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.4,
    )
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
    
    client = get_client()
    response = client.chat(
        [{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.3,
    )
    
    try:
        import json
        import re
        # Try to find JSON block if the model added text
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(response)
    except Exception:
        return {"personal": {}}
