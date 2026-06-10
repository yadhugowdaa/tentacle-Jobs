"""Tests for preferences coercion, upsert round-trip, and query building."""

import pytest
from sqlmodel import Session, SQLModel, create_engine

from tentacle_apply.db.models import Preferences, Profile, User
from tentacle_apply.discovery.preferences import (
    as_list,
    build_query,
    effective_skills,
    get_preferences,
    normalize_work_modes,
    upsert_preferences,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_as_list_variants():
    assert as_list(["a", " b ", ""]) == ["a", "b"]
    assert as_list("x, y; z") == ["x", "y", "z"]
    assert as_list(None) == []


def test_normalize_work_modes_filters_invalid():
    assert normalize_work_modes("Remote, HYBRID, banana") == ["remote", "hybrid"]
    assert normalize_work_modes(["onsite", "REMOTE"]) == ["onsite", "remote"]


def test_upsert_and_get_roundtrip(session):
    user = User(email="ada@x.com")
    session.add(user)
    session.commit()
    session.refresh(user)

    prefs = upsert_preferences(
        session, user.id, work_modes="remote,hybrid", roles="Backend Engineer", skills="python, sql"
    )
    assert prefs.work_modes == ["remote", "hybrid"]
    assert prefs.roles == ["Backend Engineer"]

    # Partial update leaves other fields intact.
    upsert_preferences(session, user.id, skills="python, sql, go")
    again = get_preferences(session, user.id)
    assert again.roles == ["Backend Engineer"]
    assert again.skills == ["python", "sql", "go"]


def test_build_query_uses_primary_role_and_drops_city_when_remote():
    prefs = Preferences(user_id=1, roles=["Backend Engineer"], work_modes=["remote"], locations=["London"])
    query, location = build_query(prefs, None)
    assert query == "Backend Engineer"
    assert location == ""  # remote → don't constrain by city


def test_build_query_keeps_city_for_onsite():
    prefs = Preferences(user_id=1, roles=["Designer"], work_modes=["onsite"], locations=["London"])
    query, location = build_query(prefs, None)
    assert query == "Designer"
    assert location == "London"


def test_effective_skills_falls_back_to_profile():
    profile = Profile(user_id=1, skills=["rust", "go"])
    assert effective_skills(None, profile) == ["rust", "go"]
    prefs = Preferences(user_id=1, skills=["python"])
    assert effective_skills(prefs, profile) == ["python"]
