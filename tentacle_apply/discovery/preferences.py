"""User search preferences: set/get + turning them into a concrete discovery query.

Preferences are the user's stated intent. Where a field is empty we fall back to the resume-derived
Profile, so discovery still works right after intake even before preferences are set.
"""

from __future__ import annotations

import re

from sqlmodel import Session, select

from tentacle_apply.db.models import Preferences, Profile, utcnow

_WORK_MODES = {"remote", "hybrid", "onsite"}


def as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [s.strip() for s in re.split(r"[,;]", value) if s.strip()]
    return []


def normalize_work_modes(value) -> list[str]:
    return [m.lower() for m in as_list(value) if m.lower() in _WORK_MODES]


def get_preferences(session: Session, user_id: int) -> Preferences | None:
    return session.exec(select(Preferences).where(Preferences.user_id == user_id)).first()


def upsert_preferences(
    session: Session,
    user_id: int,
    *,
    work_modes=None,
    locations=None,
    roles=None,
    skills=None,
    seniority: str | None = None,
    min_salary: int | None = None,
    needs_sponsorship: bool | None = None,
) -> Preferences:
    """Create or update a user's preferences. Only provided fields are changed."""
    prefs = get_preferences(session, user_id) or Preferences(user_id=user_id)
    if work_modes is not None:
        prefs.work_modes = normalize_work_modes(work_modes)
    if locations is not None:
        prefs.locations = as_list(locations)
    if roles is not None:
        prefs.roles = as_list(roles)
    if skills is not None:
        prefs.skills = as_list(skills)
    if seniority is not None:
        prefs.seniority = seniority.strip()
    if min_salary is not None:
        prefs.min_salary = min_salary
    if needs_sponsorship is not None:
        prefs.needs_sponsorship = needs_sponsorship
    prefs.updated_at = utcnow()
    session.add(prefs)
    session.commit()
    session.refresh(prefs)
    return prefs


def effective_roles(prefs: Preferences | None, profile: Profile | None) -> list[str]:
    if prefs and prefs.roles:
        return prefs.roles
    return list(profile.titles) if profile and profile.titles else []


def effective_skills(prefs: Preferences | None, profile: Profile | None) -> list[str]:
    if prefs and prefs.skills:
        return prefs.skills
    return list(profile.skills) if profile and profile.skills else []


def effective_locations(prefs: Preferences | None, profile: Profile | None) -> list[str]:
    if prefs and prefs.locations:
        return prefs.locations
    return list(profile.locations) if profile and profile.locations else []


def build_query(prefs: Preferences | None, profile: Profile | None) -> tuple[str, str]:
    """Return (query, location) for the source/aggregator search.

    The query is intentionally short — most ATS fetchers require *every* query token to appear, so
    a long query would over-filter. We use the primary role; ranking (embeddings) handles nuance.
    """
    roles = effective_roles(prefs, profile)
    query = roles[0] if roles else ""
    locations = effective_locations(prefs, profile)
    # If the user is open to remote, don't constrain the source query by city.
    work_modes = prefs.work_modes if prefs else []
    location = "" if "remote" in work_modes else (locations[0] if locations else "")
    return query, location
