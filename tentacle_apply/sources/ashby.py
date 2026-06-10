"""Ashby — public company job boards (no key) + our Tier-1 apply target.

Public posting API: https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true
Returns {"jobs": [...]} with jobUrl like https://jobs.ashbyhq.com/{org}/{id}. There's no global
search, so we pull configured org boards and filter by query locally (same model as Greenhouse).
"""

from __future__ import annotations

from tentacle_apply.sources.base import FetchedJob, http_get_json, matches_query, parse_dt, strip_html

API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def fetch_company(token: str, query: str = "", location: str = "", limit: int = 20) -> list[FetchedJob]:
    data = http_get_json(API.format(token=token), {"includeCompensation": "true"})
    out: list[FetchedJob] = []
    for j in data.get("jobs", []) if isinstance(data, dict) else []:
        if j.get("isListed") is False:
            continue
        title = j.get("title", "")
        loc = j.get("location", "") or (j.get("address") or {}).get("postalAddress", {}).get("addressLocality", "")
        desc = strip_html(j.get("descriptionHtml") or j.get("descriptionPlain", ""))
        if not matches_query(query, title, desc):
            continue
        if location and not matches_query(location, loc):
            continue
        out.append(
            FetchedJob(
                source="ashby",
                external_id=str(j.get("id", "")),
                company=token,
                title=title,
                location=loc,
                url=j.get("jobUrl", "") or j.get("applyUrl", ""),
                ats_type="ashby",
                description=desc,
                posted_at=parse_dt(j.get("publishedAt")),
            )
        )
        if len(out) >= limit:
            break
    return out


def fetch(query: str = "", location: str = "", limit: int = 20, companies: list[str] | None = None) -> list[FetchedJob]:
    from tentacle_apply.config import settings

    out: list[FetchedJob] = []
    for token in companies or getattr(settings, "ashby_companies", []):
        try:
            out.extend(fetch_company(token, query, location, limit))
        except Exception:  # noqa: BLE001 - one bad/closed board shouldn't kill the run
            continue
    return out
