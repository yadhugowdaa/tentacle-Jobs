"""Discovery run: preferences -> fetch (aggregators + registry) -> rule-filter -> store -> rank.

This is the free, zero-token half of the product. No LLM is called anywhere here; ranking uses
local embeddings. LLM spend happens later, only when the user chooses to tailor + apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import select

from tentacle_apply.db.models import Company, Profile, User
from tentacle_apply.db.session import get_session, init_db
from tentacle_apply.discovery import preferences as prefs_mod
from tentacle_apply.discovery import registry
from tentacle_apply.discovery.filters import passes_preferences
from tentacle_apply.log import get_logger
from tentacle_apply.matching.matcher import RankedJob, rank_jobs
from tentacle_apply.sources import adzuna, arbeitnow, remotive
from tentacle_apply.sources.base import FetchedJob, store_jobs

log = get_logger(__name__)

# Keyword aggregators (always on; Adzuna self-skips without a key).
_AGGREGATORS = {
    "remotive": remotive.fetch,
    "arbeitnow": arbeitnow.fetch,
    "adzuna": adzuna.fetch,
}


@dataclass
class DiscoveryReport:
    fetched: int = 0
    filtered_out: int = 0
    added: int = 0
    skipped_dup: int = 0
    companies_queried: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    ranked: list[RankedJob] = field(default_factory=list)


def _resolve_user(session, email: str | None) -> User | None:
    if email:
        return session.exec(select(User).where(User.email == email.lower())).first()
    return session.exec(select(User)).first()


def run_discovery(
    user_email: str | None = None,
    limit: int = 20,
    per_company_limit: int = 20,
    auto_seed: bool = True,
) -> DiscoveryReport:
    """Run one discovery pass for a user and return ranked, freshly-stored matches."""
    init_db()
    report = DiscoveryReport()

    with get_session() as session:
        user = _resolve_user(session, user_email)
        if user is None:
            raise ValueError("No user found. Run `intake` on a resume first.")
        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).first()
        prefs = prefs_mod.get_preferences(session, user.id)

        if auto_seed and session.exec(select(Company)).first() is None:
            registry.seed_registry(session)

        query, location = prefs_mod.build_query(prefs, profile)
        work_modes = prefs.work_modes if prefs else []
        locations = prefs_mod.effective_locations(prefs, profile)
        log.info("discovery: query=%r location=%r work_modes=%s", query, location, work_modes)

        raw: list[FetchedJob] = []

        # 1) Aggregators (broad, internet-wide).
        for name, fn in _AGGREGATORS.items():
            try:
                raw.extend(fn(query=query, location=location, limit=per_company_limit))
            except Exception as exc:  # noqa: BLE001 - record and keep going
                report.errors[name] = str(exc)[:160]
                log.warning("aggregator %s failed: %s", name, str(exc)[:160])

        # 2) Registry ATS boards (freshest; also our apply targets).
        companies = registry.list_companies(session, enabled_only=True)
        report.companies_queried = len(companies)
        for company in companies:
            raw.extend(
                registry.fetch_company_jobs(company, query=query, location=location, limit=per_company_limit)
            )
            session.add(company)  # persist last_fetched_at
        session.commit()

        report.fetched = len(raw)

        # 3) Hard rule filter (work mode / location). Soft relevance is left to ranking.
        kept: list[FetchedJob] = []
        for j in raw:
            ok, reason = passes_preferences(j, work_modes=work_modes, locations=locations)
            if ok:
                kept.append(j)
            else:
                report.filtered_out += 1
        log.info("discovery: fetched=%d kept=%d filtered_out=%d", report.fetched, len(kept), report.filtered_out)

        # 4) Store (dedupe on source+external_id).
        report.added, report.skipped_dup = store_jobs(kept)

    # 5) Rank the whole pool against the profile (local embeddings; opens its own session).
    report.ranked = rank_jobs(user_email=user_email, top=limit)
    return report
