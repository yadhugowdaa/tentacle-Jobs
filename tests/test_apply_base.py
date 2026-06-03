"""Tests for applier reliability helpers: name splitting, dedupe, and ApplyResult.ok."""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from tentacle_apply.apply.base import ApplyResult, _norm, find_duplicate, split_name
from tentacle_apply.db.models import Application, ApplicationStatus, Job, User


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_split_name():
    assert split_name("Ada Lovelace") == ("Ada", "Lovelace")
    assert split_name("Cher") == ("Cher", "")
    assert split_name("Jean Luc Picard") == ("Jean", "Luc Picard")
    assert split_name("   ") == ("", "")


def test_norm_collapses_whitespace_and_lowercases():
    assert _norm("  Senior   Engineer ") == "senior engineer"
    assert _norm(None) == ""


def test_apply_result_ok_property():
    assert ApplyResult(status=ApplicationStatus.VERIFIED).ok
    assert ApplyResult(status=ApplicationStatus.SUBMITTED).ok
    assert ApplyResult(status=ApplicationStatus.QUEUED).ok  # dry run with no error
    assert not ApplyResult(status=ApplicationStatus.QUEUED, error="boom").ok
    assert not ApplyResult(status=ApplicationStatus.FAILED).ok


def _seed(session) -> tuple[int, Job]:
    user = User(email="ada@x.com")
    session.add(user)
    session.commit()
    session.refresh(user)
    job = Job(source="greenhouse", external_id="1", company="Acme", title="Backend Engineer")
    session.add(job)
    session.commit()
    session.refresh(job)
    return user.id, job


def test_no_duplicate_when_clean(session):
    user_id, job = _seed(session)
    assert find_duplicate(session, user_id, job) is None


def test_duplicate_same_job_committed(session):
    user_id, job = _seed(session)
    session.add(Application(user_id=user_id, job_id=job.id, status=ApplicationStatus.SUBMITTED))
    session.commit()
    dup = find_duplicate(session, user_id, job)
    assert dup is not None
    assert dup.status == ApplicationStatus.SUBMITTED


def test_non_committed_status_is_not_a_duplicate(session):
    user_id, job = _seed(session)
    session.add(Application(user_id=user_id, job_id=job.id, status=ApplicationStatus.FAILED))
    session.commit()
    assert find_duplicate(session, user_id, job) is None


def test_duplicate_same_company_and_title_other_posting(session):
    user_id, job = _seed(session)
    # A second posting of the same role at the same company, via another source.
    other = Job(source="lever", external_id="99", company="ACME", title="backend engineer")
    session.add(other)
    session.commit()
    session.refresh(other)
    session.add(Application(user_id=user_id, job_id=other.id, status=ApplicationStatus.VERIFIED))
    session.commit()
    dup = find_duplicate(session, user_id, job)
    assert dup is not None
