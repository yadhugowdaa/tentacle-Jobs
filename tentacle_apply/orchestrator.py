"""Autonomous run loop (Phase B): the actual product — "keep applying until the target is hit".

Given a user + a target, one run:
  refresh the job pool (discovery) -> rank -> for each eligible Greenhouse match above the score bar:
  dedupe -> tailor (Writer/Critic) -> QUALITY GATE -> fill/upload/answer (apply) -> record,
stopping when `target` applications are prepared/submitted or the candidate pool is exhausted.

Design choices that matter for reliability:
- **Idempotent + resumable.** Progress is persisted to `Application` rows tagged with `run_id` after
  every job, and an unfinished run for the user is *resumed* rather than duplicated. A crash/restart
  picks up where it left off; already-handled jobs are skipped.
- **Quality gate, not spray.** A job is only prepared/submitted when match score, critic overall, and
  grounding all clear configured floors — protecting the user's reputation.
- **Honest submission.** Default mode is `prepare` (fill + screenshot, no submit) — the safe,
  hostable default. `submit`/`hitl` are for local single-user use; CAPTCHA is never faked.
- **Dependency-injected** appliers/ranker/tailor/session so the loop is unit-testable without a real
  browser, LLM, or embedding model.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlmodel import Session, select

from tentacle_apply.apply.assets import build_applicant, write_tailored_assets
from tentacle_apply.apply.base import find_duplicate
from tentacle_apply.apply.registry import get_applier
from tentacle_apply.config import settings
from tentacle_apply.db.models import (
    Application,
    ApplicationStatus,
    Job,
    Profile,
    Run,
    RunStatus,
    User,
    utcnow,
)
from tentacle_apply.db.session import get_session, init_db
from tentacle_apply.log import get_logger
from tentacle_apply.matching.matcher import RankedJob, rank_jobs
from tentacle_apply.tailor import TailorStudio

log = get_logger(__name__)

# Statuses that represent "a unit of work completed toward the target".
_COUNTS_PREPARE = {ApplicationStatus.QUEUED, ApplicationStatus.SUBMITTED, ApplicationStatus.VERIFIED}
_COUNTS_SUBMIT = {ApplicationStatus.SUBMITTED, ApplicationStatus.VERIFIED}
# Statuses worth retrying on a later pass (transient/again-able outcomes).
_RETRIABLE = {ApplicationStatus.FAILED, ApplicationStatus.SKIPPED_CAPTCHA}

# Injection seams (overridable in tests).
Ranker = Callable[[str | None, float], list[RankedJob]]
TailorFactory = Callable[[], TailorStudio]
ApplierResolver = Callable[[str, bool], object | None]  # (ats_type, headful) -> applier | None
SessionFactory = Callable[[], Session]


@dataclass
class JobOutcome:
    job_id: int
    title: str
    company: str
    score: float
    status: str
    reason: str = ""


@dataclass
class RunResult:
    run_id: int
    target: int
    mode: str
    prepared: int = 0          # cleared the gate and were filled (dry-run "queued") or sent
    submitted: int = 0         # actually submitted/verified
    gated_out: int = 0         # failed the quality bar
    duplicates: int = 0
    failed: int = 0
    skipped_captcha: int = 0
    unsupported: int = 0       # not a Greenhouse posting (no applier yet)
    attempts: int = 0
    stopped_reason: str = ""
    outcomes: list[JobOutcome] = field(default_factory=list)


def _default_ranker(user_email: str | None, min_score: float) -> list[RankedJob]:
    return rank_jobs(user_email=user_email, min_score=min_score, eligible_only=True)


def _job_text(job: Job) -> str:
    return f"{job.title} at {job.company}\nLocation: {job.location}\n\n{job.description}"


def _facts(profile: Profile | None) -> str:
    if profile and profile.raw_text:
        return profile.raw_text
    return ", ".join(profile.skills) if profile else ""


def _resolve_user(session: Session, email: str | None) -> User | None:
    if email:
        return session.exec(select(User).where(User.email == email.lower())).first()
    return session.exec(select(User)).first()


def _get_or_create_run(session: Session, user_id: int, target: int, mode: str) -> Run:
    """Resume an unfinished run for the user, else start a new one (never duplicate a live run)."""
    existing = session.exec(
        select(Run)
        .where(Run.user_id == user_id, Run.status == RunStatus.RUNNING)
        .order_by(Run.started_at.desc())
    ).first()
    if existing:
        log.info("resuming run id=%s (target=%s, mode=%s)", existing.id, existing.target_count, existing.mode)
        return existing
    run = Run(user_id=user_id, target_count=target, mode=mode, status=RunStatus.RUNNING)
    session.add(run)
    session.commit()
    session.refresh(run)
    log.info("started run id=%s target=%s mode=%s", run.id, target, mode)
    return run


def _progress(session: Session, run_id: int, counting: set[str]) -> int:
    apps = session.exec(select(Application).where(Application.run_id == run_id)).all()
    return sum(1 for a in apps if a.status in counting)


def _existing_application(session: Session, user_id: int, job_id: int) -> Application | None:
    return session.exec(
        select(Application).where(
            Application.user_id == user_id, Application.job_id == job_id
        )
    ).first()


@contextmanager
def _single_session(factory: SessionFactory) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()


def run_apply_loop(
    *,
    user_email: str | None = None,
    target: int | None = None,
    mode: str | None = None,
    min_score: float | None = None,
    min_critic: float | None = None,
    min_grounding: float | None = None,
    discover: bool | None = None,
    max_candidates: int | None = None,
    headful: bool = False,
    should_stop: Callable[[], bool] | None = None,
    ranker: Ranker = _default_ranker,
    tailor_factory: TailorFactory | None = None,
    applier_resolver: ApplierResolver | None = None,
    session_factory: SessionFactory = get_session,
    init: bool = True,
) -> RunResult:
    """Run one autonomous batch. Returns a RunResult summary; persists progress as it goes."""
    target = target if target is not None else settings.default_target_applications
    mode = mode or settings.run_mode
    min_score = min_score if min_score is not None else settings.min_match_score
    min_critic = min_critic if min_critic is not None else settings.min_critic_overall
    min_grounding = min_grounding if min_grounding is not None else settings.min_grounding
    discover = settings.run_discover if discover is None else discover
    max_candidates = max_candidates if max_candidates is not None else settings.run_max_candidates
    interactive = mode == "hitl"
    do_submit = mode in ("submit", "hitl")
    counting = _COUNTS_SUBMIT if do_submit else _COUNTS_PREPARE

    tailor_factory = tailor_factory or (lambda: TailorStudio())
    applier_resolver = applier_resolver or (lambda ats, hf: get_applier(ats, headful=hf))

    if init:
        init_db()

    # Refresh the pool first (its own sessions); ranking happens via the injected ranker.
    if discover:
        try:
            from tentacle_apply.discovery import run_discovery

            run_discovery(user_email=user_email, limit=max_candidates)
        except Exception as exc:  # noqa: BLE001 - discovery is best-effort; rank existing pool anyway
            log.warning("discovery failed, ranking existing pool: %s", str(exc)[:160])

    candidates = ranker(user_email, min_score)
    log.info("run: %d candidates above min_score=%.0f", len(candidates), min_score)

    with _single_session(session_factory) as session:
        user = _resolve_user(session, user_email)
        if user is None:
            raise ValueError("No user found. Run `intake` on a resume first.")
        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).first()
        run = _get_or_create_run(session, user.id, target, mode)

        result = RunResult(run_id=run.id, target=target, mode=mode)
        result.prepared = _progress(session, run.id, counting)
        facts = _facts(profile)
        studio = tailor_factory()

        for ranked in candidates:
            if result.prepared >= target:
                result.stopped_reason = "target reached"
                break
            if result.attempts >= max_candidates:
                result.stopped_reason = "max candidates reached"
                break
            if should_stop and should_stop():
                result.stopped_reason = "stopped by request"
                break

            job = session.get(Job, ranked.job.id)
            if job is None:
                continue

            # Resolve a Tier-1 applier for this job's ATS; skip if we have no template for it.
            applier = applier_resolver(job.ats_type or "", headful or interactive)
            if applier is None:
                result.unsupported += 1
                result.outcomes.append(
                    JobOutcome(job.id, job.title, job.company, ranked.score, "unsupported", f"no applier for ats={job.ats_type!r}")
                )
                continue

            dup = find_duplicate(session, user.id, job)
            if dup is not None:
                result.duplicates += 1
                result.outcomes.append(
                    JobOutcome(job.id, job.title, job.company, ranked.score, ApplicationStatus.DUPLICATE, f"dup of app #{dup.id}")
                )
                continue

            existing = _existing_application(session, user.id, job.id)
            if existing and existing.status not in _RETRIABLE:
                # Already prepared/committed (possibly on a previous pass) — count and skip.
                if existing.status in counting:
                    result.outcomes.append(
                        JobOutcome(job.id, job.title, job.company, ranked.score, existing.status, "already handled")
                    )
                continue

            result.attempts += 1
            job_text = _job_text(job)

            # 1) Tailor + quality gate.
            try:
                tailored = studio.run(job_text, facts, facts)
            except Exception as exc:  # noqa: BLE001 - a tailoring failure shouldn't abort the run
                result.failed += 1
                result.outcomes.append(
                    JobOutcome(job.id, job.title, job.company, ranked.score, ApplicationStatus.FAILED, f"tailor error: {str(exc)[:80]}")
                )
                continue

            crit = tailored.critique
            grounding = crit.scores.get("grounding", 0.0)
            if crit.overall < min_critic or grounding < min_grounding:
                result.gated_out += 1
                result.outcomes.append(
                    JobOutcome(
                        job.id, job.title, job.company, ranked.score, "gated_out",
                        f"overall={crit.overall:.0f}<{min_critic:.0f} or grounding={grounding:.0f}<{min_grounding:.0f}",
                    )
                )
                continue

            # 2) Persist assets and assemble the applicant.
            write_tailored_assets(job.id, tailored.resume, tailored.cover_letter)
            applicant = build_applicant(user, profile, job)

            # 3) Apply (dry-run/submit/hitl per mode).
            try:
                apply_res = applier.apply(
                    job.url, applicant, job_text, submit=do_submit, interactive=interactive
                )
            except Exception as exc:  # noqa: BLE001 - record a failed attempt and keep going
                apply_res = None
                log.exception("applier crashed for job_id=%s: %s", job.id, str(exc)[:160])

            status = apply_res.status if apply_res else ApplicationStatus.FAILED

            # 4) Record (idempotent upsert), tagged with this run.
            app = existing or Application(user_id=user.id, job_id=job.id)
            app.run_id = run.id
            app.status = status
            app.confirmation_url = apply_res.confirmation_url if apply_res else ""
            app.resume_version_path = str(applicant.resume_pdf or "")
            app.cover_letter = applicant.cover_letter
            app.error = (apply_res.error if apply_res else "applier crashed")[:300]
            app.attempts = (app.attempts or 0) + 1
            if apply_res and apply_res.submitted:
                app.applied_at = utcnow()
            session.add(app)

            # 5) Tally + persist progress for resumability.
            if status in _COUNTS_SUBMIT:
                result.submitted += 1
            if status in counting:
                result.prepared += 1
            elif status == ApplicationStatus.SKIPPED_CAPTCHA:
                result.skipped_captcha += 1
            elif status == ApplicationStatus.FAILED:
                result.failed += 1

            run.applied_count = result.prepared
            session.add(run)
            session.commit()
            result.outcomes.append(JobOutcome(job.id, job.title, job.company, ranked.score, status))
            log.info("job_id=%s -> %s (progress %d/%d)", job.id, status, result.prepared, target)

        if not result.stopped_reason:
            result.stopped_reason = "candidate pool exhausted"
        run.status = RunStatus.STOPPED if result.stopped_reason == "stopped by request" else RunStatus.COMPLETED
        run.applied_count = result.prepared
        run.finished_at = utcnow()
        session.add(run)
        session.commit()
        log.info("run id=%s done: %s (prepared=%d submitted=%d)", run.id, result.stopped_reason, result.prepared, result.submitted)
        return result
