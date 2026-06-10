"""Rank stored jobs against a user's profile and persist Match rows.

Score = blend of semantic similarity (embeddings) and skill overlap, 0–100. An eligibility check
(location / remote) flags jobs the user likely can't take, without discarding them outright.
LLM judgment can be layered on later for borderline cases; embeddings keep this free + fast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from sqlmodel import select

from tentacle_apply.config import settings
from tentacle_apply.db.models import Job, Match, Profile, User, utcnow
from tentacle_apply.db.session import get_session, init_db
from tentacle_apply.log import get_logger
from tentacle_apply.matching.embedder import embed

log = get_logger(__name__)

_REMOTE_HINTS = ("remote", "worldwide", "anywhere", "global")

# Common English function words: their density cleanly separates English text (~0.15–0.35) from other
# languages (~<0.05). Free, dependency-free language signal — good enough to catch e.g. German posts.
_EN_COMMON = frozenset(
    "the a an and or of to in for with on at as is are be will we you your our this that "
    "from by it they have has not can".split()
)


def _english_ratio(text: str) -> float:
    toks = re.findall(r"[a-z]+", (text or "").lower())
    if not toks:
        return 0.0
    return sum(1 for t in toks if t in _EN_COMMON) / len(toks)


def _language_penalty(profile_text: str, job_text: str) -> float:
    """0<penalty<=1. Penalize a posting whose language doesn't match the resume's (English resume vs
    a clearly non-English posting). Symmetric-ish: if the resume itself isn't English, don't penalize.
    """
    if settings.lang_mismatch_penalty >= 1.0:
        return 1.0
    profile_en = _english_ratio(profile_text) >= 0.10
    job_non_en = _english_ratio(job_text) < 0.09
    return settings.lang_mismatch_penalty if (profile_en and job_non_en) else 1.0


@dataclass
class RankedJob:
    job: Job
    score: float
    eligible: bool
    reason: str


def _profile_text(p: Profile) -> str:
    parts: list[str] = []
    if p.titles:
        parts.append("Roles: " + ", ".join(p.titles))
    if p.skills:
        parts.append("Skills: " + ", ".join(p.skills))
    if p.years_exp:
        parts.append(f"{p.years_exp} years experience")
    if p.raw_text:
        parts.append(p.raw_text[:1200])
    return "\n".join(parts) or "software engineer"


def _job_text(j: Job) -> str:
    return f"{j.title}\n{j.company}\n{j.location}\n{j.description[:1800]}"


def _skill_overlap(skills: list[str], text_lower: str) -> float:
    if not skills:
        return 0.0
    hits = sum(1 for s in skills if s.lower() in text_lower)
    return min(1.0, hits / len(skills))


def _eligible(profile_locations: list[str], job_location: str) -> tuple[bool, str]:
    if not profile_locations:
        return True, ""
    jl = (job_location or "").lower()
    if not jl.strip():
        return True, ""  # unknown location → don't exclude
    if any(h in jl for h in _REMOTE_HINTS):
        return True, ""
    if any(loc.lower() in jl for loc in profile_locations):
        return True, ""
    return False, f"location '{job_location}' outside preferences"


def _cosine(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query / (np.linalg.norm(query) + 1e-9)
    m = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
    return m @ q


def rank_jobs(
    user_email: str | None = None,
    top: int | None = None,
    min_score: float = 0.0,
    eligible_only: bool = False,
) -> list[RankedJob]:
    init_db()
    with get_session() as session:
        if user_email:
            user = session.exec(select(User).where(User.email == user_email.lower())).first()
        else:
            user = session.exec(select(User)).first()
        if user is None:
            raise ValueError("No user found. Run `intake` on a resume first.")

        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).first()
        if profile is None:
            raise ValueError("No profile found for user. Run `intake` first.")

        jobs = list(session.exec(select(Job)))
        if not jobs:
            log.info("no jobs to rank; run `sources` first")
            return []

        log.info("ranking %d jobs for user_id=%s", len(jobs), user.id)
        profile_text = _profile_text(profile)
        job_texts = [_job_text(j) for j in jobs]
        vectors = embed([profile_text] + job_texts)
        sims = _cosine(vectors[0], vectors[1:])

        results: list[RankedJob] = []
        for job, text, sim in zip(jobs, job_texts, sims, strict=False):
            overlap = _skill_overlap(profile.skills, text.lower())
            penalty = _language_penalty(profile_text, text)
            score = round(100.0 * (0.7 * float(sim) + 0.3 * overlap) * penalty, 1)
            eligible, reason = _eligible(profile.locations, job.location)
            if penalty < 1.0 and not reason:
                reason = "posting language differs from resume (down-ranked)"
            results.append(RankedJob(job=job, score=score, eligible=eligible, reason=reason))

        # Upsert Match rows.
        for r in results:
            m = session.exec(
                select(Match).where(Match.user_id == user.id, Match.job_id == r.job.id)
            ).first()
            if m is None:
                m = Match(user_id=user.id, job_id=r.job.id)
            m.score = r.score
            m.eligible = r.eligible
            m.reason = r.reason
            m.created_at = utcnow()
            session.add(m)
        session.commit()

        ranked = sorted(results, key=lambda r: r.score, reverse=True)
        if eligible_only:
            ranked = [r for r in ranked if r.eligible]
        ranked = [r for r in ranked if r.score >= min_score]
        return ranked[:top] if top else ranked
