"""Workable — public company job boards (no key) + our Tier-1 apply target.

Modern Workable boards (apply.workable.com/{token}) are SPAs backed by a public JSON API:
  - list:   POST https://apply.workable.com/api/v3/accounts/{token}/jobs   -> {"results": [...]}
  - detail: GET  https://apply.workable.com/api/v1/accounts/{token}/jobs/{shortcode} -> description
There's no global search, so we pull a configured board and filter by query locally. The hosted
posting/apply pages live at https://apply.workable.com/{token}/j/{shortcode}/.
"""

from __future__ import annotations

from tentacle_apply.sources.base import (
    SESSION,
    TIMEOUT,
    FetchedJob,
    matches_query,
    parse_dt,
    strip_html,
)

LIST_API = "https://apply.workable.com/api/v3/accounts/{token}/jobs"
DETAIL_API = "https://apply.workable.com/api/v1/accounts/{token}/jobs/{shortcode}"
JOB_URL = "https://apply.workable.com/{token}/j/{shortcode}/"


def _loc(job: dict) -> str:
    loc = job.get("location") or {}
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    text = ", ".join(p for p in parts if p)
    if job.get("remote") and "remote" not in text.lower():
        text = f"Remote · {text}" if text else "Remote"
    return text


def _description(token: str, shortcode: str) -> str:
    """One detail fetch for the full posting text (Workable list omits descriptions)."""
    try:
        resp = SESSION.get(
            DETAIL_API.format(token=token, shortcode=shortcode),
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return ""
        d = resp.json()
        return strip_html(" ".join(filter(None, (d.get("description"), d.get("requirements"), d.get("benefits")))))
    except Exception:  # noqa: BLE001 - description is best-effort; matching still works on title/loc
        return ""


def fetch_company(token: str, query: str = "", location: str = "", limit: int = 20) -> list[FetchedJob]:
    resp = SESSION.post(
        LIST_API.format(token=token),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json={"query": query or ""},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json().get("results", []) if isinstance(resp.json(), dict) else []

    out: list[FetchedJob] = []
    for j in results:
        if j.get("state") and j.get("state") != "published":
            continue
        shortcode = j.get("shortcode", "")
        title = j.get("title", "")
        loc = _loc(j)
        if location and not matches_query(location, loc):
            continue
        desc = _description(token, shortcode) if shortcode else ""
        if not matches_query(query, title, desc, " ".join(j.get("department", []) or [])):
            continue
        out.append(
            FetchedJob(
                source="workable",
                external_id=str(j.get("id") or shortcode),
                company=token,
                title=title,
                location=loc,
                url=JOB_URL.format(token=token, shortcode=shortcode),
                ats_type="workable",
                description=desc,
                posted_at=parse_dt(j.get("published")),
            )
        )
        if len(out) >= limit:
            break
    return out


def fetch(query: str = "", location: str = "", limit: int = 20, companies: list[str] | None = None) -> list[FetchedJob]:
    from tentacle_apply.config import settings

    out: list[FetchedJob] = []
    for token in companies or getattr(settings, "workable_companies", []):
        try:
            out.extend(fetch_company(token, query, location, limit))
        except Exception:  # noqa: BLE001 - one bad/closed board shouldn't kill the run
            continue
    return out
