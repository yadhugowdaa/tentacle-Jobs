"""Shared types + reliability helpers for the applier tier (dedupe, screenshots, applicant data)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from tentacle_apply.config import DATA_DIR
from tentacle_apply.db.models import Application, ApplicationStatus, Job

SCREENSHOT_DIR = DATA_DIR / "screenshots"

# Statuses that mean "we already committed an application here" — used for dedupe.
_COMMITTED = {ApplicationStatus.SUBMITTED, ApplicationStatus.VERIFIED}


@dataclass
class Applicant:
    """The real, grounded data we type into a form. Never invented."""

    first_name: str
    last_name: str
    email: str
    phone: str = ""
    location: str = ""
    work_auth: str = ""
    min_salary: int | None = None
    years_exp: float = 0.0
    resume_pdf: Path | None = None
    resume_text: str = ""  # real resume text, for grounding free-text answers
    cover_letter: str = ""
    links: dict[str, str] = field(default_factory=dict)


@dataclass
class ApplyResult:
    status: str
    job_id: int | None = None
    confirmation_url: str = ""
    screenshot: str = ""
    error: str = ""
    filled: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    submitted: bool = False

    @property
    def ok(self) -> bool:
        return self.status in (ApplicationStatus.VERIFIED, ApplicationStatus.SUBMITTED) or (
            self.status == ApplicationStatus.QUEUED and not self.error
        )


def split_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def screenshot_path(job_id: int | None, tag: str = "apply") -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return SCREENSHOT_DIR / f"job{job_id}_{tag}_{stamp}.png"


def find_duplicate(session: Session, user_id: int, job: Job) -> Application | None:
    """Already applied to this exact job, or to the same role at the same company?

    Either case means we must NOT apply again (protects the user from looking like a spammer).
    """
    same_job = session.exec(
        select(Application).where(
            Application.user_id == user_id, Application.job_id == job.id
        )
    ).all()
    for app in same_job:
        if app.status in _COMMITTED:
            return app

    # Same company + title via another source/posting.
    candidates = session.exec(
        select(Application).where(Application.user_id == user_id)
    ).all()
    for app in candidates:
        if app.status not in _COMMITTED:
            continue
        other = session.get(Job, app.job_id)
        if other and _norm(other.company) == _norm(job.company) and _norm(other.title) == _norm(job.title):
            return app
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())
