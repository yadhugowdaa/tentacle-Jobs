"""Intake: turn an uploaded resume into a structured, stored profile."""

from tentacle_apply.intake.profile import ProfileData, extract_profile, ingest_resume

__all__ = ["ProfileData", "extract_profile", "ingest_resume"]
