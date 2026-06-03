"""Adzuna — global job aggregator API (free key required). Skipped automatically if no key.

https://api.adzuna.com/v1/api/jobs/{country}/search/1
"""

from __future__ import annotations

from tentacle_apply.sources.base import FetchedJob, http_get_json, parse_dt, strip_html

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"


def is_configured() -> bool:
    from tentacle_apply.config import settings

    return bool(settings.adzuna_app_id and settings.adzuna_app_key)


def fetch(query: str = "", location: str = "", limit: int = 20) -> list[FetchedJob]:
    from tentacle_apply.config import settings

    if not is_configured():
        return []
    params = {
        "app_id": settings.adzuna_app_id,
        "app_key": settings.adzuna_app_key,
        "results_per_page": min(limit, 50),
        "what": query,
        "content-type": "application/json",
    }
    if location:
        params["where"] = location
    data = http_get_json(API.format(country=settings.adzuna_country), params)
    out: list[FetchedJob] = []
    for j in data.get("results", []):
        out.append(
            FetchedJob(
                source="adzuna",
                external_id=str(j.get("id", "")),
                company=(j.get("company") or {}).get("display_name", ""),
                title=j.get("title", ""),
                location=(j.get("location") or {}).get("display_name", ""),
                url=j.get("redirect_url", ""),
                ats_type="external",  # Adzuna links out; apply path resolved later
                description=strip_html(j.get("description", "")),
                posted_at=parse_dt(j.get("created")),
            )
        )
        if len(out) >= limit:
            break
    return out
