"""Applier tier: deterministic ATS form automation (Playwright), reliability-first.

Default mode is a DRY RUN: we navigate, fill every field, and screenshot the completed form, but
stop before the final submit. This is both the safe way to develop/test (no fake submissions to
real companies) and a genuine product feature (human-in-the-loop "review before submit").
"""

from tentacle_apply.apply.ashby import AshbyApplier
from tentacle_apply.apply.base import Applier, ApplyResult, find_duplicate, screenshot_path
from tentacle_apply.apply.greenhouse import GreenhouseApplier
from tentacle_apply.apply.lever import LeverApplier
from tentacle_apply.apply.registry import get_applier, supported_ats
from tentacle_apply.apply.smartrecruiters import SmartRecruitersApplier
from tentacle_apply.apply.workable import WorkableApplier
from tentacle_apply.apply.workday import WorkdayApplier

__all__ = [
    "Applier",
    "ApplyResult",
    "AshbyApplier",
    "GreenhouseApplier",
    "LeverApplier",
    "SmartRecruitersApplier",
    "WorkableApplier",
    "WorkdayApplier",
    "find_duplicate",
    "get_applier",
    "screenshot_path",
    "supported_ats",
]
