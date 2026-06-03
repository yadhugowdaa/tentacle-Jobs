"""Pure, deterministic pre-ranking filters (no DB, no network, no LLM — easy to unit-test).

Philosophy: filter only on things we can judge *confidently* from a posting (work mode, location).
Everything fuzzy (how relevant is this role?) is left to the embedding ranker, so we don't wrongly
discard good matches. Returns (kept, reason) so the caller can log why something was dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

_REMOTE_HINTS = ("remote", "worldwide", "anywhere", "global", "distributed")
_ONSITE_HINTS = ("on-site", "onsite", "in office", "in-office")


@dataclass
class JobLike:
    """Minimal shape the filters need (FetchedJob and Job both satisfy this)."""

    title: str
    location: str
    description: str


def _looks_remote(text: str) -> bool:
    return any(h in text for h in _REMOTE_HINTS)


def location_matches(preferred: list[str], job_location: str) -> bool:
    """True if no preference, the location is unknown, or any preferred place appears in it."""
    if not preferred:
        return True
    jl = (job_location or "").lower().strip()
    if not jl:
        return True  # unknown location → don't exclude
    return any(loc.lower() in jl for loc in preferred)


def passes_work_mode(work_modes: list[str], job: JobLike) -> bool:
    """Keep the job if it plausibly fits any selected work mode.

    We can only detect 'remote' reliably from text; hybrid/onsite are inferred as 'not clearly
    remote-only', so we keep them unless the user *only* wants remote and the post isn't remote.
    """
    if not work_modes:
        return True
    blob = f"{job.location} {job.title} {job.description[:400]}".lower()
    remote = _looks_remote(blob)
    if "remote" in work_modes and remote:
        return True
    if ("hybrid" in work_modes or "onsite" in work_modes) and not (
        "remote" in work_modes and len(work_modes) == 1
    ):
        # User accepts an in-person mode → location filter (applied separately) does the gating.
        return True
    # User wants remote-only and the post isn't clearly remote.
    return remote


def passes_preferences(
    job: JobLike,
    *,
    work_modes: list[str] | None = None,
    locations: list[str] | None = None,
) -> tuple[bool, str]:
    """Combined hard filter. Soft relevance is deferred to ranking."""
    work_modes = work_modes or []
    locations = locations or []

    if not passes_work_mode(work_modes, job):
        return False, "work mode mismatch (wanted remote-only)"

    # Location only gates in-person modes; remote-accepting users aren't constrained by city.
    if locations and "remote" not in work_modes:
        blob = f"{job.location} {job.description[:200]}".lower()
        if not location_matches(locations, job.location) and not _looks_remote(blob):
            return False, f"location '{job.location}' outside preferences"

    return True, ""
