"""Company registry: the heart of discovery.

A `Company` row says "pull jobs from this ATS board (and it's an apply target too)". We grow the
registry two free ways:
  - **seed**: ship a starter list (`seed_companies.py`).
  - **user-added**: resolve a company name or careers URL to (ats, token) deterministically —
    parse it straight from a board URL, or probe the public ATS APIs with slug guesses.

No LLM, no paid search. Invalid tokens are harmless (the fetcher just skips empty boards).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import requests
from sqlmodel import Session, select

from tentacle_apply.db.models import Company, utcnow
from tentacle_apply.discovery.seed_companies import SEED_COMPANIES
from tentacle_apply.log import get_logger
from tentacle_apply.sources import greenhouse, lever
from tentacle_apply.sources.base import USER_AGENT, FetchedJob

log = get_logger(__name__)

# ats -> function(token, query, location, limit) -> list[FetchedJob]
ATS_FETCH = {
    "greenhouse": greenhouse.fetch_company,
    "lever": lever.fetch_company,
}

# ats -> a probe URL template used to verify a guessed token exists.
_PROBE_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
}

# Patterns that pull (ats, token) straight out of a careers/board URL.
_URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/(?:v1/boards/)?([\w-]+)", re.I)),
    ("lever", re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([\w-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w-]+)", re.I)),
]

SUPPORTED_ATS = tuple(ATS_FETCH.keys())


def _slugify_variants(name: str) -> list[str]:
    base = name.strip().lower()
    compact = re.sub(r"[^a-z0-9]+", "", base)
    hyphen = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    # Preserve order, drop empties/dupes.
    return list(dict.fromkeys(v for v in (compact, hyphen) if v))


def parse_company_url(raw: str) -> tuple[str, str] | None:
    """If `raw` is a recognizable ATS board URL, return (ats, token); else None."""
    for ats, pat in _URL_PATTERNS:
        m = pat.search(raw)
        if m:
            token = m.group(1)
            if token and token not in {"v1", "v0", "embed"}:
                return ats, token
    return None


def _token_exists(ats: str, token: str) -> bool:
    url = _PROBE_URL[ats].format(token=token)
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=15)
        if resp.status_code != 200:
            return False
        data = resp.json()
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        return bool(jobs)
    except Exception:  # noqa: BLE001 - a failed probe just means "not this ATS"
        return False


def resolve_company(raw: str) -> tuple[str, str, str] | None:
    """Resolve a name or careers URL to (ats, token, display_name), or None if not found.

    Order: (1) parse a board URL directly; (2) probe supported ATS APIs with slug guesses.
    """
    raw = raw.strip()
    if not raw:
        return None

    parsed = parse_company_url(raw)
    if parsed:
        ats, token = parsed
        if ats in ATS_FETCH and _token_exists(ats, token):
            return ats, token, token
        return None

    name = raw
    for token in _slugify_variants(name):
        for ats in SUPPORTED_ATS:
            if _token_exists(ats, token):
                log.info("resolved %r -> %s/%s", name, ats, token)
                return ats, token, name
    log.info("could not resolve company %r on supported ATS %s", name, SUPPORTED_ATS)
    return None


def seed_registry(session: Session) -> int:
    """Insert any seed companies not already present. Returns the number added."""
    added = 0
    for ats, token, name in SEED_COMPANIES:
        if ats not in ATS_FETCH:
            continue
        exists = session.exec(
            select(Company).where(Company.ats == ats, Company.token == token)
        ).first()
        if exists:
            continue
        session.add(Company(name=name, ats=ats, token=token, origin="seed"))
        added += 1
    session.commit()
    if added:
        log.info("seeded %d companies into the registry", added)
    return added


def add_company(session: Session, raw: str) -> Company | None:
    """Resolve and upsert a user-supplied company (name or URL). Returns the Company or None."""
    resolved = resolve_company(raw)
    if resolved is None:
        return None
    ats, token, name = resolved
    existing = session.exec(
        select(Company).where(Company.ats == ats, Company.token == token)
    ).first()
    if existing:
        existing.enabled = True
        session.add(existing)
        session.commit()
        return existing
    company = Company(name=name, ats=ats, token=token, origin="user")
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def list_companies(session: Session, enabled_only: bool = False) -> list[Company]:
    stmt = select(Company).order_by(Company.name)
    if enabled_only:
        stmt = stmt.where(Company.enabled == True)  # noqa: E712 - SQLModel needs == for SQL
    return list(session.exec(stmt))


def fetch_company_jobs(
    company: Company, query: str = "", location: str = "", limit: int = 20
) -> list[FetchedJob]:
    """Pull jobs for one registry company via its ATS fetcher. Empty list on any failure."""
    fn = ATS_FETCH.get(company.ats)
    if fn is None:
        return []
    try:
        jobs = fn(token=company.token, query=query, location=location, limit=limit)
    except Exception as exc:  # noqa: BLE001 - one bad board must not kill discovery
        log.warning("registry fetch failed for %s/%s: %s", company.ats, company.token, str(exc)[:120])
        return []
    company.last_fetched_at = datetime.now(UTC)
    return jobs


# Re-export utcnow for callers that update timestamps alongside registry ops.
__all__ = [
    "ATS_FETCH",
    "SUPPORTED_ATS",
    "add_company",
    "fetch_company_jobs",
    "list_companies",
    "parse_company_url",
    "resolve_company",
    "seed_registry",
    "utcnow",
]
