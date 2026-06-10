"""Build the grounded `Applicant` + per-job application assets (tailored resume PDF, cover letter).

Shared by both the manual `cli apply` path and the autonomous orchestrator so the two never drift.
Everything here is derived from the user's real profile + a tailored draft — never invented.
"""

from __future__ import annotations

import re
from pathlib import Path

from tentacle_apply.apply.base import Applicant, split_name
from tentacle_apply.config import DATA_DIR
from tentacle_apply.db.models import Job, Profile, User
from tentacle_apply.tailor.render import markdown_to_pdf

TAILORED_DIR = DATA_DIR / "tailored"


def extract_phone(text: str) -> str:
    m = re.search(r"(\+?\d[\d\s().\-]{7,}\d)", text or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def extract_links(text: str) -> dict[str, str]:
    links: dict[str, str] = {}
    for kind, pat in (
        ("linkedin", r"(?:https?://)?(?:www\.)?linkedin\.com/[\w\-/]+"),
        ("github", r"(?:https?://)?(?:www\.)?github\.com/[\w\-/]+"),
    ):
        m = re.search(pat, text or "", re.I)
        if m:
            links[kind] = m.group(0)
    return links


def write_tailored_assets(job_id: int, resume_md: str, cover_text: str) -> tuple[Path, Path]:
    """Persist the tailored resume (md) + cover letter (txt) for a job. Returns (resume_md, cover_txt)."""
    TAILORED_DIR.mkdir(parents=True, exist_ok=True)
    resume_path = TAILORED_DIR / f"job{job_id}_resume.md"
    cover_path = TAILORED_DIR / f"job{job_id}_cover.txt"
    resume_path.write_text(resume_md, encoding="utf-8")
    cover_path.write_text(cover_text, encoding="utf-8")
    return resume_path, cover_path


def resume_pdf_for(profile: Profile | None, job_id: int) -> Path | None:
    """Best available resume PDF: tailored-for-this-job → original PDF resume → rendered raw text."""
    tailored_md = TAILORED_DIR / f"job{job_id}_resume.md"
    if tailored_md.exists():
        return markdown_to_pdf(
            tailored_md.read_text(encoding="utf-8"), TAILORED_DIR / f"job{job_id}_resume.pdf"
        )
    if (
        profile
        and profile.resume_path
        and Path(profile.resume_path).suffix.lower() == ".pdf"
        and Path(profile.resume_path).exists()
    ):
        return Path(profile.resume_path)
    if profile and profile.raw_text:
        return markdown_to_pdf(
            profile.raw_text, TAILORED_DIR / f"profile_{profile.user_id}_resume.pdf"
        )
    return None


def read_cover_letter(job_id: int) -> str:
    cover_path = TAILORED_DIR / f"job{job_id}_cover.txt"
    return cover_path.read_text(encoding="utf-8") if cover_path.exists() else ""


def build_applicant(user: User, profile: Profile | None, job: Job | None) -> Applicant:
    """Assemble the data we type into a form from the user + profile + any tailored assets on disk."""
    first, last = split_name(profile.full_name if profile else "")
    job_id = job.id if job else 0
    raw = profile.raw_text if profile else ""
    return Applicant(
        first_name=first,
        last_name=last,
        email=user.email,
        phone=extract_phone(raw),
        location=(profile.locations[0] if profile and profile.locations else ""),
        work_auth=(profile.work_auth if profile else ""),
        min_salary=(profile.min_salary if profile else None),
        years_exp=(profile.years_exp if profile else 0.0),
        resume_pdf=resume_pdf_for(profile, job_id),
        resume_text=raw,
        cover_letter=read_cover_letter(job_id),
        links=extract_links(raw),
    )
