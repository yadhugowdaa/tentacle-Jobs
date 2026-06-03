"""Matching: score stored jobs against the user's profile (RAG retrieval half) + eligibility."""

from tentacle_apply.matching.matcher import RankedJob, rank_jobs

__all__ = ["RankedJob", "rank_jobs"]
