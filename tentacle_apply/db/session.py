"""Engine + session helpers. SQLite by default; set DATABASE_URL for Postgres."""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from tentacle_apply.config import DATA_DIR, RESUMES_DIR, settings
from tentacle_apply.db import models  # noqa: F401 - registers tables on SQLModel.metadata

_is_sqlite = settings.db_url.startswith("sqlite")
_engine = create_engine(
    settings.db_url,
    echo=False,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)


def init_db() -> None:
    """Create data dirs (for SQLite + resumes) and all tables if missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(_engine)


def get_session() -> Session:
    # expire_on_commit=False so already-loaded rows stay usable after the session closes
    # (we read jobs, compute, then commit Match rows and still return the job data).
    return Session(_engine, expire_on_commit=False)
