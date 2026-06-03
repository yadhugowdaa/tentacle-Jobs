"""Remotive — remote jobs API (no key). https://remotive.com/api/remote-jobs"""

from __future__ import annotations

from tentacle_apply.sources.base import FetchedJob, http_get_json, matches_query, parse_dt, strip_html

API = "https://remotive.com/api/remote-jobs"


def fetch(query: str = "", location: str = "", limit: int = 20) -> list[FetchedJob]:
    data = http_get_json(API, {"search": query, "limit": limit})
    out: list[FetchedJob] = []
    for j in data.get("jobs", []):
        loc = j.get("candidate_required_location", "Remote")
        if location and not matches_query(location, loc):
            continue
        out.append(
            FetchedJob(
                source="remotive",
                external_id=str(j.get("id", "")),
                company=j.get("company_name", ""),
                title=j.get("title", ""),
                location=loc,
                url=j.get("url", ""),
                ats_type="remotive",
                description=strip_html(j.get("description", "")),
                posted_at=parse_dt(j.get("publication_date")),
            )
        )
        if len(out) >= limit:
            break
    return out
