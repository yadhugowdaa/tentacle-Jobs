"""Arbeitnow — free job board API (no key). https://www.arbeitnow.com/api/job-board-api"""

from __future__ import annotations

from tentacle_apply.sources.base import FetchedJob, http_get_json, matches_query, parse_dt, strip_html

API = "https://www.arbeitnow.com/api/job-board-api"


def fetch(query: str = "", location: str = "", limit: int = 20) -> list[FetchedJob]:
    data = http_get_json(API)
    out: list[FetchedJob] = []
    for j in data.get("data", []):
        title = j.get("title", "")
        desc = strip_html(j.get("description", ""))
        loc = j.get("location", "")
        if not matches_query(query, title, desc, ", ".join(j.get("tags", []))):
            continue
        if location and not matches_query(location, loc) and not j.get("remote"):
            continue
        out.append(
            FetchedJob(
                source="arbeitnow",
                external_id=str(j.get("slug", "")),
                company=j.get("company_name", ""),
                title=title,
                location=loc or ("Remote" if j.get("remote") else ""),
                url=j.get("url", ""),
                ats_type="arbeitnow",
                description=desc,
                posted_at=parse_dt(j.get("created_at")),
            )
        )
        if len(out) >= limit:
            break
    return out
