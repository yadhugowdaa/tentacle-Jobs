"""Tests for the autonomous run loop's decision logic.

The orchestrator is dependency-injected so we can exercise every branch (quality gate, dedupe,
unsupported ATS, target stop, resume, submit vs prepare counting) with NO real browser, LLM, or
embedding model — just fakes + an in-memory SQLite DB.
"""

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from tentacle_apply import orchestrator
from tentacle_apply.apply.base import Applicant, ApplyResult
from tentacle_apply.db.models import (
    Application,
    ApplicationStatus,
    Job,
    Profile,
    Run,
    RunStatus,
    User,
)
from tentacle_apply.matching.matcher import RankedJob
from tentacle_apply.tailor.critic import Critique
from tentacle_apply.tailor.studio import TailorResult


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def factory(engine):
    return lambda: Session(engine, expire_on_commit=False)


@pytest.fixture
def seeded(engine, factory, monkeypatch):
    """A user + profile, and asset/applicant builders stubbed so no files/PDFs are touched."""
    monkeypatch.setattr(orchestrator, "write_tailored_assets", lambda job_id, r, c: (None, None))
    monkeypatch.setattr(
        orchestrator,
        "build_applicant",
        lambda user, profile, job: Applicant(first_name="A", last_name="B", email=user.email),
    )
    with factory() as s:
        user = User(email="ada@x.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        s.add(Profile(user_id=user.id, full_name="Ada B", raw_text="python, fastapi"))
        s.commit()
    return user.id


def _job(s: Session, ats="greenhouse", title="Backend Engineer", company="Acme") -> Job:
    job = Job(
        source=ats, external_id=str(id(title) % 100000), company=company, title=title,
        location="Remote", url="https://x/apply", ats_type=ats, description="build things",
    )
    s.add(job)
    s.commit()
    s.refresh(job)
    return job


class FakeStudio:
    def __init__(self, overall=90.0, grounding=95.0):
        self.overall, self.grounding = overall, grounding

    def run(self, job_text, resume_text, facts=None):
        crit = Critique(
            overall=self.overall,
            scores={"relevance": self.overall, "keyword_coverage": self.overall,
                    "grounding": self.grounding, "clarity": self.overall},
        )
        return TailorResult(resume="# resume", cover_letter="cover", critique=crit, history=[self.overall])


class FakeApplier:
    def __init__(self, status=ApplicationStatus.QUEUED):
        self.status, self.calls = status, 0

    def apply(self, url, applicant, job_text="", submit=False, interactive=False):
        self.calls += 1
        submitted = submit and self.status in (ApplicationStatus.SUBMITTED, ApplicationStatus.VERIFIED)
        return ApplyResult(status=self.status, submitted=submitted)


def _run(factory, ranked, *, studio=None, applier=None, supported=("greenhouse",), **kw):
    studio = studio or FakeStudio()
    applier = applier or FakeApplier()

    def resolver(ats, headful):
        return applier if ats in supported else None

    defaults = dict(
        user_email="ada@x.com", discover=False, init=False,
        min_score=0.0, min_critic=70.0, min_grounding=80.0,
        ranker=lambda email, ms: ranked,
        tailor_factory=lambda: studio,
        applier_resolver=resolver,
        session_factory=factory,
    )
    defaults.update(kw)
    return orchestrator.run_apply_loop(**defaults), applier


def test_prepares_until_target_then_stops(factory, seeded):
    with factory() as s:
        ranked = [RankedJob(job=_job(s, title=f"Role {i}"), score=80.0, eligible=True, reason="") for i in range(3)]
    applier = FakeApplier(ApplicationStatus.QUEUED)
    result, applier = _run(factory, ranked, applier=applier, target=2)
    assert result.prepared == 2
    assert result.attempts == 2  # third candidate never attempted
    assert applier.calls == 2
    assert result.stopped_reason == "target reached"
    with factory() as s:
        assert len(s.exec(select(Application)).all()) == 2


def test_quality_gate_blocks_and_does_not_call_applier(factory, seeded):
    with factory() as s:
        ranked = [RankedJob(job=_job(s), score=90.0, eligible=True, reason="")]
    applier = FakeApplier()
    result, applier = _run(factory, ranked, studio=FakeStudio(overall=50.0, grounding=95.0), applier=applier, target=5)
    assert result.gated_out == 1
    assert result.prepared == 0
    assert applier.calls == 0
    with factory() as s:
        assert s.exec(select(Application)).all() == []


def test_low_grounding_is_gated(factory, seeded):
    with factory() as s:
        ranked = [RankedJob(job=_job(s), score=90.0, eligible=True, reason="")]
    result, _ = _run(factory, ranked, studio=FakeStudio(overall=90.0, grounding=40.0), target=5)
    assert result.gated_out == 1
    assert result.prepared == 0


def test_unsupported_ats_is_skipped(factory, seeded):
    with factory() as s:
        ranked = [RankedJob(job=_job(s, ats="workday"), score=90.0, eligible=True, reason="")]
    applier = FakeApplier()
    # Only greenhouse/lever supported here; workday has no applier -> unsupported, not attempted.
    result, applier = _run(factory, ranked, applier=applier, supported=("greenhouse", "lever"), target=5)
    assert result.unsupported == 1
    assert applier.calls == 0


def test_duplicate_committed_job_is_skipped(factory, seeded):
    with factory() as s:
        job = _job(s)
        s.add(Application(user_id=seeded, job_id=job.id, status=ApplicationStatus.VERIFIED))
        s.commit()
        ranked = [RankedJob(job=job, score=90.0, eligible=True, reason="")]
    applier = FakeApplier()
    result, applier = _run(factory, ranked, applier=applier, target=5)
    assert result.duplicates == 1
    assert applier.calls == 0


def test_submit_mode_counts_only_submitted(factory, seeded):
    with factory() as s:
        ranked = [RankedJob(job=_job(s), score=90.0, eligible=True, reason="")]
    applier = FakeApplier(ApplicationStatus.SKIPPED_CAPTCHA)
    result, applier = _run(factory, ranked, applier=applier, mode="submit", target=5)
    assert result.skipped_captcha == 1
    assert result.prepared == 0
    assert result.submitted == 0
    assert applier.calls == 1


def test_run_is_marked_completed(factory, seeded):
    with factory() as s:
        ranked = [RankedJob(job=_job(s), score=90.0, eligible=True, reason="")]
    result, _ = _run(factory, ranked, target=5)
    with factory() as s:
        run = s.get(Run, result.run_id)
        assert run.status == RunStatus.COMPLETED
        assert run.finished_at is not None


def test_running_run_is_resumed_not_duplicated(factory, seeded):
    with factory() as s:
        s.add(Run(user_id=seeded, target_count=5, mode="prepare", status=RunStatus.RUNNING))
        s.commit()
        ranked = [RankedJob(job=_job(s), score=90.0, eligible=True, reason="")]
    result, _ = _run(factory, ranked, target=5)
    with factory() as s:
        runs = s.exec(select(Run)).all()
        assert len(runs) == 1  # resumed the existing RUNNING run
        assert runs[0].id == result.run_id


def test_already_prepared_job_is_skipped_not_reapplied(factory, seeded):
    with factory() as s:
        job = _job(s)
        s.add(Application(user_id=seeded, job_id=job.id, status=ApplicationStatus.QUEUED))
        s.commit()
        ranked = [RankedJob(job=job, score=90.0, eligible=True, reason="")]
    applier = FakeApplier()
    result, applier = _run(factory, ranked, applier=applier, target=5)
    assert applier.calls == 0  # not re-applied
