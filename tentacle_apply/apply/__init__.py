"""Applier tier: deterministic ATS form automation (Playwright), reliability-first.

Default mode is a DRY RUN: we navigate, fill every field, and screenshot the completed form, but
stop before the final submit. This is both the safe way to develop/test (no fake submissions to
real companies) and a genuine product feature (human-in-the-loop "review before submit").
"""

from tentacle_apply.apply.base import ApplyResult, find_duplicate, screenshot_path
from tentacle_apply.apply.greenhouse import GreenhouseApplier

__all__ = ["ApplyResult", "GreenhouseApplier", "find_duplicate", "screenshot_path"]
