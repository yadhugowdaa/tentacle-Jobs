"""Shared types + helpers for job-source adapters.

Every adapter returns a list of `FetchedJob` (a normalized shape). `store_jobs` upserts them into
the DB, de-duplicating on (source, external_id) so re-running a search never creates duplicates.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import requests
from sqlmodel import select

from tentacle_apply.db.models import Job
from tentacle_apply.db.session import get_session, init_db

USER_AGENT = "tentacle-apply/0.1 (+https://github.com/octopodia)"
TIMEOUT = 20


@dataclass
class FetchedJob:
    source: str
    external_id: str
    company: str
    title: str
    location: str
    url: str
    ats_type: str = ""
    description: str = ""
    posted_at: datetime | None = None


def http_get_json(url: str, params: dict | None = None):
    resp = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def matches_query(query: str, *fields: str) -> bool:
    """True if no query, or every whitespace-token of the query appears in the combined fields."""
    if not query:
        return True
    haystack = " ".join(f for f in fields if f).lower()
    return all(tok in haystack for tok in query.lower().split())


def store_jobs(jobs: list[FetchedJob]) -> tuple[int, int]:
    """Insert new jobs; skip ones already stored. Returns (added, skipped)."""
    init_db()
    added = skipped = 0
    with get_session() as session:
        for j in jobs:
            exists = session.exec(
                select(Job).where(Job.source == j.source, Job.external_id == str(j.external_id))
            ).first()
            if exists:
                skipped += 1
                continue
            session.add(
                Job(
                    source=j.source,
                    external_id=str(j.external_id),
                    company=j.company,
                    title=j.title,
                    location=j.location,
                    url=j.url,
                    ats_type=j.ats_type or j.source,
                    description=j.description,
                    posted_at=j.posted_at,
                )
            )
            added += 1
        session.commit()
    return added, skipped
