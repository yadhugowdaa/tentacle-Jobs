"""ATS → Tier-1 applier registry.

One place that maps a job's `ats_type` to the deterministic template that can fill it. The run loop
and CLI resolve an applier here instead of hardcoding a single ATS, so adding a new ATS is a one-line
registration.
"""

from __future__ import annotations

from tentacle_apply.apply.ashby import AshbyApplier
from tentacle_apply.apply.base import Applier
from tentacle_apply.apply.greenhouse import GreenhouseApplier
from tentacle_apply.apply.lever import LeverApplier
from tentacle_apply.apply.smartrecruiters import SmartRecruitersApplier
from tentacle_apply.apply.workable import WorkableApplier
from tentacle_apply.apply.workday import WorkdayApplier

_APPLIERS: dict[str, type] = {
    "greenhouse": GreenhouseApplier,
    "lever": LeverApplier,
    "ashby": AshbyApplier,
    "workable": WorkableApplier,
    "smartrecruiters": SmartRecruitersApplier,
    "workday": WorkdayApplier,
}


def supported_ats() -> tuple[str, ...]:
    return tuple(_APPLIERS)


def get_applier(
    ats_type: str, headful: bool = False, timeout_ms: int = 30000, hitl_timeout_s: int = 300
) -> Applier | None:
    """Return a Tier-1 applier for this ATS, or None if we have no template for it."""
    cls = _APPLIERS.get((ats_type or "").lower())
    if cls is None:
        return None
    return cls(headful=headful, timeout_ms=timeout_ms, hitl_timeout_s=hitl_timeout_s)
