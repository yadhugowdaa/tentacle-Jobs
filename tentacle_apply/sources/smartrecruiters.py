"""SmartRecruiters — public company postings (no key) + our Tier-1 apply target.

Public Posting API (read-only, no auth for customers who enable it):
  - list:   GET https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=&q=
  - detail: GET https://api.smartrecruiters.com/v1/companies/{token}/postings/{id}  -> jobAd + applyUrl
The list omits descriptions, so we fetch each posting's detail for grounding text. Hosted posting
pages live at https://jobs.smartrecruiters.com/{token}/{id}.
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

LIST_API = "https://api.smartrecruiters.com/v1/companies/{token}/postings"
DETAIL_API = "https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}"
JOB_URL = "https://jobs.smartrecruiters.com/{token}/{posting_id}"


def _loc(posting: dict) -> str:
    loc = posting.get("location") or {}
    text = loc.get("fullLocation") or ", ".join(
        p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p
    )
    if loc.get("remote") and "remote" not in (text or "").lower():
        text = f"Remote · {text}" if text else "Remote"
    return text


def _detail(token: str, posting_id: str) -> tuple[str, str]:
    """Return (description, apply_url) from a posting's detail; ('', '') on failure."""
    try:
        resp = SESSION.get(
            DETAIL_API.format(token=token, posting_id=posting_id),
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return "", ""
        d = resp.json()
        sections = (d.get("jobAd") or {}).get("sections") or {}
        text = " ".join(
            (sections.get(k) or {}).get("text", "")
            for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
        )
        return strip_html(text), d.get("applyUrl") or d.get("postingUrl") or ""
    except Exception:  # noqa: BLE001 - best-effort; matching still works on title/department
        return "", ""


def fetch_company(token: str, query: str = "", location: str = "", limit: int = 20) -> list[FetchedJob]:
    resp = SESSION.get(
        LIST_API.format(token=token),
        params={"limit": max(limit, 10), "q": query or ""},
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    content = resp.json().get("content", []) if isinstance(resp.json(), dict) else []

    out: list[FetchedJob] = []
    for p in content:
        title = p.get("name", "")
        loc = _loc(p)
        if location and not matches_query(location, loc):
            continue
        posting_id = str(p.get("id", ""))
        dept = (p.get("department") or {}).get("label", "")
        desc, apply_url = _detail(token, posting_id) if posting_id else ("", "")
        if not matches_query(query, title, desc, dept):
            continue
        out.append(
            FetchedJob(
                source="smartrecruiters",
                external_id=posting_id or p.get("uuid", ""),
                company=(p.get("company") or {}).get("identifier", token),
                title=title,
                location=loc,
                url=apply_url or JOB_URL.format(token=token, posting_id=posting_id),
                ats_type="smartrecruiters",
                description=desc,
                posted_at=parse_dt(p.get("releasedDate")),
            )
        )
        if len(out) >= limit:
            break
    return out


def fetch(query: str = "", location: str = "", limit: int = 20, companies: list[str] | None = None) -> list[FetchedJob]:
    from tentacle_apply.config import settings

    out: list[FetchedJob] = []
    for token in companies or getattr(settings, "smartrecruiters_companies", []):
        try:
            out.extend(fetch_company(token, query, location, limit))
        except Exception:  # noqa: BLE001 - one bad/closed board shouldn't kill the run
            continue
    return out
