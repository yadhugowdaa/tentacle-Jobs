"""Tracker — read models for the dashboard: per-user application list + summary stats.

Pure aggregation over the DB (no side effects). Joins each Application to its Job so the UI can
show company / role / location / link without N+1 lookups on the client.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from tentacle_apply.db.models import Application, ApplicationStatus, Job, Run, User

# Statuses that count as "the application actually went out".
_DONE = {ApplicationStatus.SUBMITTED, ApplicationStatus.VERIFIED}
# Statuses that mean "prepared / in flight", i.e. work done but not yet sent.
_PREPARED = {ApplicationStatus.QUEUED, ApplicationStatus.TAILORING, ApplicationStatus.APPLYING}


def resolve_user(session: Session, email: str | None) -> User | None:
    if email:
        return session.exec(select(User).where(User.email == email.lower())).first()
    return session.exec(select(User)).first()


def _app_row(session: Session, app: Application) -> dict[str, Any]:
    job = session.get(Job, app.job_id)
    return {
        "id": app.id,
        "company": job.company if job else "",
        "title": job.title if job else "",
        "location": job.location if job else "",
        "job_url": job.url if job else "",
        "status": app.status,
        "confirmation_url": app.confirmation_url,
        "resume_path": app.resume_version_path,
        "attempts": app.attempts,
        "error": app.error,
        "applied_at": app.applied_at.isoformat() if app.applied_at else None,
        "created_at": app.created_at.isoformat() if app.created_at else None,
    }


def list_applications(session: Session, user: User) -> list[dict[str, Any]]:
    apps = session.exec(
        select(Application)
        .where(Application.user_id == user.id)
        .order_by(Application.created_at.desc())
    ).all()
    return [_app_row(session, a) for a in apps]


def latest_run(session: Session, user: User) -> dict[str, Any]:
    """Most recent run for the user (or an empty shell if none yet)."""
    run = session.exec(
        select(Run).where(Run.user_id == user.id).order_by(Run.started_at.desc())
    ).first()
    if run is None:
        return {"run_id": None, "status": None, "mode": None, "target": 0, "applied": 0}
    return {
        "run_id": run.id,
        "status": run.status,
        "mode": run.mode,
        "target": run.target_count,
        "applied": run.applied_count,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def stats(session: Session, user: User) -> dict[str, Any]:
    apps = session.exec(select(Application).where(Application.user_id == user.id)).all()
    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a.status] = by_status.get(a.status, 0) + 1

    submitted = sum(by_status.get(s, 0) for s in _DONE)
    prepared = sum(by_status.get(s, 0) for s in _PREPARED)
    skipped = by_status.get(ApplicationStatus.SKIPPED_CAPTCHA, 0)
    failed = by_status.get(ApplicationStatus.FAILED, 0)

    run = session.exec(
        select(Run).where(Run.user_id == user.id).order_by(Run.started_at.desc())
    ).first()
    target = run.target_count if run else 0

    return {
        "total": len(apps),
        "submitted": submitted,
        "prepared": prepared,
        "skipped_captcha": skipped,
        "failed": failed,
        "target": target,
        "by_status": by_status,
    }
