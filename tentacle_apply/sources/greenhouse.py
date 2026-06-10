"""Greenhouse — public company job boards (no key) + our Tier-1 apply target.

Per-company endpoint: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
There's no global search, so we pull configured company boards and filter by the query locally.
"""

from __future__ import annotations

from tentacle_apply.sources.base import FetchedJob, http_get_json, matches_query, parse_dt, strip_html

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def ats_type_for_url(url: str) -> str:
    """Only Greenhouse-hosted forms (`*.greenhouse.io`) are Tier-1 applyable.

    Many companies use Greenhouse as a backend but render a fully custom front-end on their own
    domain (e.g. stripe.com, databricks.com). Those can't be driven by our deterministic template,
    so we tag them `greenhouse_external` — the run loop will skip them instead of wasting tailoring
    budget on a form it can't fill.
    """
    return "greenhouse" if "greenhouse.io" in (url or "").lower() else "greenhouse_external"


def fetch_company(token: str, query: str = "", location: str = "", limit: int = 20) -> list[FetchedJob]:
    data = http_get_json(API.format(token=token), {"content": "true"})
    out: list[FetchedJob] = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        loc = (j.get("location") or {}).get("name", "")
        desc = strip_html(j.get("content", ""))
        if not matches_query(query, title, desc):
            continue
        if location and not matches_query(location, loc):
            continue
        url = j.get("absolute_url", "")
        out.append(
            FetchedJob(
                source="greenhouse",
                external_id=str(j.get("id", "")),
                company=token,
                title=title,
                location=loc,
                url=url,
                ats_type=ats_type_for_url(url),
                description=desc,
                posted_at=parse_dt(j.get("updated_at")),
            )
        )
        if len(out) >= limit:
            break
    return out


def fetch(query: str = "", location: str = "", limit: int = 20, companies: list[str] | None = None) -> list[FetchedJob]:
    from tentacle_apply.config import settings

    out: list[FetchedJob] = []
    for token in companies or settings.greenhouse_companies:
        try:
            out.extend(fetch_company(token, query, location, limit))
        except Exception:  # noqa: BLE001 - one bad/closed board shouldn't kill the run
            continue
    return out
