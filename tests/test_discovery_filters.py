"""Tests for the deterministic discovery pre-ranking filters."""

from tentacle_apply.discovery.filters import (
    JobLike,
    location_matches,
    passes_preferences,
    passes_work_mode,
)


def _job(title="Engineer", location="", description=""):
    return JobLike(title=title, location=location, description=description)


def test_location_matches_defaults():
    assert location_matches([], "Anywhere")          # no preference
    assert location_matches(["London"], "")           # unknown location not excluded
    assert location_matches(["London"], "London, UK")
    assert not location_matches(["London"], "Tokyo, Japan")


def test_work_mode_no_preference_keeps_everything():
    assert passes_work_mode([], _job(location="Tokyo"))


def test_remote_only_keeps_remote_drops_onsite():
    assert passes_work_mode(["remote"], _job(location="Remote - Worldwide"))
    assert not passes_work_mode(["remote"], _job(location="Tokyo, Japan"))


def test_accepting_inperson_modes_keeps_nonremote():
    assert passes_work_mode(["remote", "hybrid"], _job(location="Berlin"))
    assert passes_work_mode(["onsite"], _job(location="Berlin"))


def test_passes_preferences_remote_only_rejects_onsite():
    ok, reason = passes_preferences(_job(location="Tokyo"), work_modes=["remote"])
    assert not ok
    assert "work mode" in reason


def test_passes_preferences_location_gate_for_onsite():
    ok, _ = passes_preferences(_job(location="Tokyo"), work_modes=["onsite"], locations=["London"])
    assert not ok
    ok2, _ = passes_preferences(_job(location="London, UK"), work_modes=["onsite"], locations=["London"])
    assert ok2


def test_passes_preferences_remote_user_not_location_gated():
    # Remote-accepting users shouldn't be filtered by city.
    ok, _ = passes_preferences(
        _job(location="Remote (US)", description="fully remote"),
        work_modes=["remote"],
        locations=["London"],
    )
    assert ok
