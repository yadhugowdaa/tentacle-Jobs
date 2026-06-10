"""Database models (SQLModel). Plural table names so they're Postgres-safe later.

The schema mirrors the pipeline: a User has a Profile; Jobs are fetched from sources; a Match
scores a Job against a user; an Application records one apply attempt and its verified outcome;
a Run tracks a batch toward a target count.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class ApplicationStatus:
    """Lifecycle of one application attempt."""

    QUEUED = "queued"
    TAILORING = "tailoring"
    APPLYING = "applying"
    SUBMITTED = "submitted"          # we clicked submit
    VERIFIED = "verified"            # confirmation captured
    FAILED = "failed"
    SKIPPED_CAPTCHA = "skipped_captcha"
    DUPLICATE = "duplicate"


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)


class Profile(SQLModel, table=True):
    __tablename__ = "profiles"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    full_name: str = ""
    skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    years_exp: float = 0.0
    titles: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    locations: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    work_auth: str = ""
    min_salary: int | None = None
    resume_path: str = ""
    raw_text: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class Preferences(SQLModel, table=True):
    """User-stated job-search intent (distinct from the resume-derived Profile).

    Profile = what the candidate has done; Preferences = what they want. Discovery filters and
    ranks against these, falling back to Profile-derived values when a field is left empty.
    """

    __tablename__ = "preferences"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, unique=True)
    work_modes: list[str] = Field(default_factory=list, sa_column=Column(JSON))  # remote|hybrid|onsite
    locations: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    roles: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    seniority: str = ""
    min_salary: int | None = None
    needs_sponsorship: bool = False
    updated_at: datetime = Field(default_factory=utcnow)


class Company(SQLModel, table=True):
    """One entry in the discovery registry: an ATS board we can pull jobs from (and apply to).

    `token` is the company's slug within its ATS (e.g. Greenhouse `anthropic`). Unique per
    (ats, token) so seeding + user-adds never duplicate.
    """

    __tablename__ = "companies"

    id: int | None = Field(default=None, primary_key=True)
    name: str = ""
    ats: str = Field(index=True)              # "greenhouse" | "lever" | ...
    token: str = Field(index=True)            # slug within the ATS
    enabled: bool = True
    origin: str = "seed"                      # "seed" | "user"
    last_fetched_at: datetime | None = None
    added_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)          # e.g. "greenhouse", "adzuna"
    external_id: str = Field(index=True)     # id within the source
    company: str = ""
    title: str = ""
    location: str = ""
    url: str = ""
    ats_type: str = ""                       # "greenhouse" | "lever" | ... (apply target)
    description: str = ""
    posted_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)


class Match(SQLModel, table=True):
    __tablename__ = "matches"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    score: float = 0.0
    eligible: bool = True
    reason: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Application(SQLModel, table=True):
    __tablename__ = "applications"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    run_id: int | None = Field(default=None, foreign_key="runs.id", index=True)
    status: str = Field(default=ApplicationStatus.QUEUED, index=True)
    applied_at: datetime | None = None
    confirmation_url: str = ""
    resume_version_path: str = ""
    cover_letter: str = ""
    error: str = ""
    attempts: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class RunStatus:
    """Lifecycle of one autonomous batch run."""

    RUNNING = "running"
    COMPLETED = "completed"   # reached target or exhausted the candidate pool cleanly
    STOPPED = "stopped"       # asked to stop / cancelled
    FAILED = "failed"         # aborted on an unexpected error


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    target_count: int = 20
    applied_count: int = 0
    mode: str = "prepare"                       # prepare | submit | hitl
    status: str = Field(default=RunStatus.RUNNING, index=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
