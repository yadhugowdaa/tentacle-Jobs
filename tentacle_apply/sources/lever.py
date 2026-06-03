"""Lever — public company postings (no key) + our Tier-1 apply target.

Per-company endpoint: https://api.lever.co/v0/postings/{token}?mode=json
"""

from __future__ import annotations

from tentacle_apply.sources.base import FetchedJob, http_get_json, matches_query, parse_dt, strip_html

API = "https://api.lever.co/v0/postings/{token}"


def fetch_company(token: str, query: str = "", location: str = "", limit: int = 20) -> list[FetchedJob]:
    data = http_get_json(API.format(token=token), {"mode": "json"})
    out: list[FetchedJob] = []
    for j in data if isinstance(data, list) else []:
        title = j.get("text", "")
        loc = (j.get("categories") or {}).get("location", "")
        desc = strip_html(j.get("descriptionPlain") or j.get("description", ""))
        if not matches_query(query, title, desc):
            continue
        if location and not matches_query(location, loc):
            continue
        out.append(
            FetchedJob(
                source="lever",
                external_id=str(j.get("id", "")),
                company=token,
                title=title,
                location=loc,
                url=j.get("hostedUrl", ""),
                ats_type="lever",
                description=desc,
                posted_at=parse_dt(j.get("createdAt")),
            )
        )
        if len(out) >= limit:
            break
    return out


def fetch(query: str = "", location: str = "", limit: int = 20, companies: list[str] | None = None) -> list[FetchedJob]:
    from tentacle_apply.config import settings

    out: list[FetchedJob] = []
    for token in companies or settings.lever_companies:
        try:
            out.extend(fetch_company(token, query, location, limit))
        except Exception:  # noqa: BLE001
            continue
    return out
