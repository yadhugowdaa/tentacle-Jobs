"""Job sources: pluggable adapters + an aggregator that fetches and stores listings.

`fetch_jobs` runs every enabled source, tolerates individual failures, de-duplicates, and stores
new jobs in the DB. Each source is just a module exposing `fetch(query, location, limit)`.
"""

from __future__ import annotations

from dataclasses import dataclass

from tentacle_apply.log import get_logger
from tentacle_apply.sources import adzuna, arbeitnow, greenhouse, lever, remotive
from tentacle_apply.sources.base import FetchedJob, store_jobs

log = get_logger(__name__)

# Keyword-searchable sources (always on; Adzuna self-skips without a key).
_KEYWORD_SOURCES = {
    "remotive": remotive.fetch,
    "arbeitnow": arbeitnow.fetch,
    "adzuna": adzuna.fetch,
}
# Company-board sources (pull configured company tokens; also our apply targets).
_BOARD_SOURCES = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
}


@dataclass
class FetchReport:
    fetched: int
    added: int
    skipped: int
    errors: dict[str, str]
    jobs: list[FetchedJob]


def fetch_jobs(query: str = "", location: str = "", limit: int = 20, store: bool = True) -> FetchReport:
    jobs: list[FetchedJob] = []
    errors: dict[str, str] = {}

    log.info("fetching jobs query=%r location=%r limit=%d", query, location, limit)
    for name, fn in {**_KEYWORD_SOURCES, **_BOARD_SOURCES}.items():
        try:
            found = fn(query=query, location=location, limit=limit)
            jobs.extend(found)
            log.debug("source %s returned %d jobs", name, len(found))
        except Exception as exc:  # noqa: BLE001 - record and keep going
            errors[name] = str(exc)[:200]
            log.warning("source %s failed: %s", name, str(exc)[:200])

    added = skipped = 0
    if store:
        added, skipped = store_jobs(jobs)
    log.info("fetched=%d added=%d skipped(dup)=%d errors=%d", len(jobs), added, skipped, len(errors))
    return FetchReport(fetched=len(jobs), added=added, skipped=skipped, errors=errors, jobs=jobs)


__all__ = ["fetch_jobs", "FetchReport", "FetchedJob", "store_jobs"]
