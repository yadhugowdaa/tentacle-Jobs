"""Resume text -> structured Profile (LLM extraction) -> persisted to the DB.

We use the *stable* model at temperature 0 so extraction is deterministic, and a defensive
coercion layer so messy model output (a skill as a string, salary as "120k", etc.) never crashes.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field
from sqlmodel import select

from tentacle_apply.db.models import Profile, User, utcnow
from tentacle_apply.db.session import get_session, init_db
from tentacle_apply.intake.extract_text import extract_text
from tentacle_apply.llm import complete
from tentacle_apply.structured import parse_json

EXTRACT_PROMPT = """You extract a structured candidate profile from resume text.
Return ONLY a JSON object with EXACTLY these keys:
{{"full_name": "", "email": "", "skills": [], "years_exp": 0, "titles": [], "locations": [], "work_auth": "", "min_salary": null, "summary": ""}}

Rules:
- skills: concrete technical/professional skills (max ~20).
- years_exp: total years of professional experience as a number (estimate from dates if needed).
- titles: job titles held or clearly targeted.
- locations: cities/countries the candidate is in or open to.
- work_auth: any work-authorization / visa info stated, else "".
- min_salary: integer if a salary expectation is stated, else null.
- summary: one concise sentence describing the candidate.
Use ONLY information present in the resume; do not invent. Output JSON only.

RESUME:
{resume}"""


class ProfileData(BaseModel):
    full_name: str = ""
    email: str = ""
    skills: list[str] = Field(default_factory=list)
    years_exp: float = 0.0
    titles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    work_auth: str = ""
    min_salary: int | None = None
    summary: str = ""


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [s.strip() for s in re.split(r"[,;]", value) if s.strip()]
    return []


def _as_float(value) -> float:
    try:
        return float(re.sub(r"[^0-9.]", "", str(value)) or 0)
    except ValueError:
        return 0.0


def _as_salary(value) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).lower().replace(",", "").strip()
    mult = 1000 if "k" in text else 1
    digits = re.sub(r"[^0-9.]", "", text)
    if not digits:
        return None
    try:
        return int(float(digits) * mult)
    except ValueError:
        return None


def _coerce(data: dict) -> ProfileData:
    return ProfileData(
        full_name=str(data.get("full_name", "")).strip(),
        email=str(data.get("email", "")).strip(),
        skills=_as_list(data.get("skills")),
        years_exp=_as_float(data.get("years_exp")),
        titles=_as_list(data.get("titles")),
        locations=_as_list(data.get("locations")),
        work_auth=str(data.get("work_auth", "")).strip(),
        min_salary=_as_salary(data.get("min_salary")),
        summary=str(data.get("summary", "")).strip(),
    )


def extract_profile(resume_text: str) -> ProfileData:
    """Run the LLM extractor over resume text and return a structured, coerced ProfileData."""
    raw = complete(EXTRACT_PROMPT.format(resume=resume_text[:8000]), stable=True, temperature=0.0)
    return _coerce(parse_json(raw))


def ingest_resume(path: str | Path, email: str | None = None) -> Profile:
    """Full intake: extract text -> structured profile -> upsert User + Profile in the DB."""
    init_db()
    text = extract_text(path)
    if not text.strip():
        raise ValueError("No text could be extracted from the resume (is it a scanned image?).")

    pdata = extract_profile(text)
    user_email = (email or pdata.email or "me@local").strip().lower()

    with get_session() as session:
        user = session.exec(select(User).where(User.email == user_email)).first()
        if user is None:
            user = User(email=user_email)
            session.add(user)
            session.commit()
            session.refresh(user)

        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).first()
        if profile is None:
            profile = Profile(user_id=user.id)

        profile.full_name = pdata.full_name
        profile.skills = pdata.skills
        profile.years_exp = pdata.years_exp
        profile.titles = pdata.titles
        profile.locations = pdata.locations
        profile.work_auth = pdata.work_auth
        profile.min_salary = pdata.min_salary
        profile.resume_path = str(path)
        profile.raw_text = text
        profile.updated_at = utcnow()

        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile
