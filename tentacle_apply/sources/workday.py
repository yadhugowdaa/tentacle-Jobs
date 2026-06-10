"""Workday — public CXS jobs API (no key) + our (honesty-first) apply target.

Workday powers a huge share of enterprise career sites, each on its own tenant host
`{tenant}.{dc}.myworkdayjobs.com/{site}` (e.g. `nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite`).
The site exposes an unauthenticated JSON API we use for discovery:
  - list:   POST https://{host}/wday/cxs/{tenant}/{site}/jobs   body {limit, offset, searchText, appliedFacets}
  - detail: GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}  -> jobPostingInfo (HTML description)

Token shape we store/resolve: "{host}/{site}" (it carries everything: host + tenant=host.split('.')[0] + site).

Honesty note: Workday *discovery* is fully supported here. Workday *apply*, however, gates every
submission behind creating a per-employer account (email + password), which we deliberately do NOT
automate — so the applier reports the gate honestly instead of faking a submit (see apply/workday.py).
"""

from __future__ import annotations

from tentacle_apply.sources.base import (
    SESSION,
    TIMEOUT,
    FetchedJob,
    matches_query,
    strip_html,
)

_JSON_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def parse_token(token: str) -> tuple[str, str, str] | None:
    """"{host}/{site}" -> (host, tenant, site); None if it isn't a Workday token."""
    token = (token or "").strip().strip("/")
    if "myworkdayjobs.com" not in token or "/" not in token:
        return None
    host, _, rest = token.partition("/")
    site = rest.split("/")[0]
    tenant = host.split(".")[0]
    if not (host and site and tenant):
        return None
    return host, tenant, site


def cxs_base(host: str, tenant: str, site: str) -> str:
    return f"https://{host}/wday/cxs/{tenant}/{site}"


def _detail(host: str, tenant: str, site: str, external_path: str) -> tuple[str, str]:
    """Return (description, precise_location) from a posting's CXS detail; ('', '') on failure."""
    try:
        resp = SESSION.get(
            f"{cxs_base(host, tenant, site)}{external_path}",
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return "", ""
        info = (resp.json().get("jobPostingInfo") or {})
        return strip_html(info.get("jobDescription")), (info.get("location") or "")
    except Exception:  # noqa: BLE001 - best-effort; matching still works on the title
        return "", ""


def fetch_company(token: str, query: str = "", location: str = "", limit: int = 20) -> list[FetchedJob]:
    parsed = parse_token(token)
    if parsed is None:
        return []
    host, tenant, site = parsed

    resp = SESSION.post(
        f"{cxs_base(host, tenant, site)}/jobs",
        headers=_JSON_HEADERS,
        json={"appliedFacets": {}, "limit": max(limit, 20), "offset": 0, "searchText": query or ""},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    postings = resp.json().get("jobPostings", []) if isinstance(resp.json(), dict) else []

    out: list[FetchedJob] = []
    for p in postings:
        title = p.get("title", "")
        external_path = p.get("externalPath", "")
        if not external_path:
            continue
        bullets = p.get("bulletFields") or []
        req_id = str(bullets[0]) if bullets else external_path
        desc, loc = _detail(host, tenant, site, external_path)
        loc = loc or p.get("locationsText", "")
        if location and not matches_query(location, loc):
            continue
        if not matches_query(query, title, desc):
            continue
        out.append(
            FetchedJob(
                source="workday",
                external_id=req_id,
                company=tenant.replace("-", " ").title(),
                title=title,
                location=loc,
                url=f"https://{host}/{site}{external_path}",
                ats_type="workday",
                description=desc,
            )
        )
        if len(out) >= limit:
            break
    return out


def fetch(query: str = "", location: str = "", limit: int = 20, companies: list[str] | None = None) -> list[FetchedJob]:
    from tentacle_apply.config import settings

    out: list[FetchedJob] = []
    for token in companies or getattr(settings, "workday_companies", []):
        try:
            out.extend(fetch_company(token, query, location, limit))
        except Exception:  # noqa: BLE001 - one bad/closed board shouldn't kill the run
            continue
    return out
