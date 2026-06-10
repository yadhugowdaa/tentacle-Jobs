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

from sqlmodel import Session, select

from tentacle_apply.db.models import Company, utcnow
from tentacle_apply.discovery import detect
from tentacle_apply.discovery.seed_companies import SEED_COMPANIES
from tentacle_apply.log import get_logger
from tentacle_apply.sources import ashby, greenhouse, lever, smartrecruiters, workable, workday
from tentacle_apply.sources.base import SESSION, FetchedJob

log = get_logger(__name__)

# ats -> function(token, query, location, limit) -> list[FetchedJob]
ATS_FETCH = {
    "greenhouse": greenhouse.fetch_company,
    "lever": lever.fetch_company,
    "ashby": ashby.fetch_company,
    "workable": workable.fetch_company,
    "smartrecruiters": smartrecruiters.fetch_company,
    "workday": workday.fetch_company,
}

# ats -> a probe URL template used to verify a guessed token exists (simple GET + truthy JSON).
_PROBE_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}


def _workable_token_exists(token: str) -> bool:
    """Workable's board list is a POST, so it needs a custom probe (GET won't do)."""
    try:
        resp = SESSION.post(
            workable.LIST_API.format(token=token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={},
            timeout=15,
        )
        return resp.status_code == 200 and bool(resp.json().get("results"))
    except Exception:  # noqa: BLE001 - a failed probe just means "not this ATS"
        return False


def _smartrecruiters_token_exists(token: str) -> bool:
    """SmartRecruiters answers 200 even for unknown companies, so require a real posting."""
    try:
        resp = SESSION.get(
            smartrecruiters.LIST_API.format(token=token),
            params={"limit": 1},
            headers={"Accept": "application/json"},
            timeout=15,
        )
        return resp.status_code == 200 and bool(resp.json().get("content"))
    except Exception:  # noqa: BLE001 - a failed probe just means "not this ATS"
        return False


def _workday_token_exists(token: str) -> bool:
    """Workday tokens are "{host}/{site}"; verify by POSTing the tenant's CXS jobs endpoint."""
    parsed = workday.parse_token(token)
    if parsed is None:
        return False
    host, tenant, site = parsed
    try:
        resp = SESSION.post(
            f"{workday.cxs_base(host, tenant, site)}/jobs",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
            timeout=15,
        )
        return resp.status_code == 200 and bool(resp.json().get("jobPostings"))
    except Exception:  # noqa: BLE001 - a failed probe just means "not this ATS"
        return False


# ats -> custom callable(token) -> bool, for boards the generic GET probe can't verify.
_PROBE_FN = {
    "workable": _workable_token_exists,
    "smartrecruiters": _smartrecruiters_token_exists,
    "workday": _workday_token_exists,
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


def _looks_like_url(raw: str) -> bool:
    """A pasted careers page (vs. a bare company name): has a scheme or a `domain.tld` shape."""
    return bool(re.match(r"^https?://", raw, re.I)) or bool(re.search(r"\.[a-z]{2,}(?:/|$|\?)", raw, re.I))


def _domain_label(raw: str) -> str:
    """Registrable label from a URL/host (careers.acme.com -> 'acme'); falls back to `raw`."""
    host = re.sub(r"^https?://", "", raw.strip(), flags=re.I).split("/")[0].split("?")[0].split(":")[0]
    parts = [p for p in host.split(".") if p]
    while parts and parts[0].lower() in {"www", "careers", "career", "jobs", "job", "apply", "boards", "work", "join", "hire", "hiring"}:
        parts.pop(0)
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else raw


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
    custom = _PROBE_FN.get(ats)
    if custom is not None:
        return custom(token)
    template = _PROBE_URL.get(ats)
    if template is None:
        return False
    url = template.format(token=token)
    try:
        resp = SESSION.get(url, headers={"Accept": "application/json"}, timeout=15)
        if resp.status_code != 200:
            return False
        data = resp.json()
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        return bool(jobs)
    except Exception:  # noqa: BLE001 - a failed probe just means "not this ATS"
        return False


def _verified(ats: str, token: str) -> bool:
    return ats in ATS_FETCH and _token_exists(ats, token)


def _display_for(ats: str, token: str) -> str:
    """A human-friendly name from a token. Workday tokens are "{host}/{site}" → use the tenant."""
    if ats == "workday":
        return token.split("/")[0].split(".")[0].replace("-", " ").title()
    return token


def resolve_company(raw: str) -> tuple[str, str, str] | None:
    """Resolve a name or *any* careers URL to (ats, token, display_name), or None if not found.

    Order (cheapest first):
      1. Board token straight out of the pasted string (board URL or obvious ATS embed host).
      2. **Tier-0 detect**: if it's a URL, fetch the page and fingerprint the embedded ATS.
      3. Slug-guess across supported ATS — by the URL's domain label, else the bare name.
    """
    raw = raw.strip()
    if not raw:
        return None

    # 1) Token visible in the string itself (e.g. jobs.lever.co/acme, ...greenhouse.io/embed?for=acme).
    #    If it's clearly a known ATS board URL, a failed verify means "dead board" — stop here rather
    #    than fall through to domain-label guessing (which would extract the ATS host, not a company).
    parsed = parse_company_url(raw) or detect.detect_in_text(raw)
    if parsed:
        ats, token = parsed
        if _verified(ats, token):
            return ats, token, _display_for(ats, token)
        return None

    if _looks_like_url(raw):
        # 2) Fetch the careers page and fingerprint the ATS it's wired to.
        detected = detect.detect_ats(raw)
        if detected:
            ats, token = detected
            if _verified(ats, token):
                log.info("resolved %r -> %s/%s via Tier-0 detect", raw, ats, token)
                return ats, token, _domain_label(raw)
        # 3a) No embed found — try the domain label as a slug on each ATS.
        label = _domain_label(raw)
        for token in _slugify_variants(label):
            for ats in SUPPORTED_ATS:
                if _token_exists(ats, token):
                    log.info("resolved %r -> %s/%s via domain-label guess", raw, ats, token)
                    return ats, token, label
        log.info("could not resolve careers URL %r on supported ATS %s", raw, SUPPORTED_ATS)
        return None

    # 3b) Plain company name → slug-guess across supported ATS.
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
