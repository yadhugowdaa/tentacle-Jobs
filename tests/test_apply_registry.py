"""ATS applier registry: resolve the right Tier-1 template by ats_type."""

from tentacle_apply.apply import get_applier, supported_ats
from tentacle_apply.apply.ashby import AshbyApplier
from tentacle_apply.apply.base import Applier
from tentacle_apply.apply.greenhouse import GreenhouseApplier
from tentacle_apply.apply.lever import LeverApplier
from tentacle_apply.apply.lever import _apply_url as lever_apply_url
from tentacle_apply.apply.smartrecruiters import SmartRecruitersApplier
from tentacle_apply.apply.workable import WorkableApplier
from tentacle_apply.apply.workable import _apply_url as workable_apply_url
from tentacle_apply.apply.workday import WorkdayApplier


def test_supported_ats_covers_tier1():
    assert set(supported_ats()) == {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "workday"}


def test_get_applier_returns_correct_type():
    assert isinstance(get_applier("greenhouse"), GreenhouseApplier)
    assert isinstance(get_applier("lever"), LeverApplier)
    assert isinstance(get_applier("ashby"), AshbyApplier)
    assert isinstance(get_applier("workable"), WorkableApplier)
    assert isinstance(get_applier("smartrecruiters"), SmartRecruitersApplier)
    assert isinstance(get_applier("workday"), WorkdayApplier)


def test_get_applier_is_case_insensitive_and_unknown_is_none():
    assert isinstance(get_applier("Greenhouse"), GreenhouseApplier)
    assert get_applier("greenhouse_external") is None
    assert get_applier("taleo") is None
    assert get_applier("") is None


def test_appliers_satisfy_protocol():
    for ats in supported_ats():
        applier = get_applier(ats)
        assert isinstance(applier, Applier)
        assert applier.ats == ats


def test_lever_apply_url_normalization():
    assert lever_apply_url("https://jobs.lever.co/acme/123") == "https://jobs.lever.co/acme/123/apply"
    assert lever_apply_url("https://jobs.lever.co/acme/123/apply") == "https://jobs.lever.co/acme/123/apply"
    assert lever_apply_url("https://jobs.lever.co/acme/123/?utm=x") == "https://jobs.lever.co/acme/123/apply"


def test_workable_apply_url_normalization():
    assert workable_apply_url("https://apply.workable.com/acme/j/ABC123/") == "https://apply.workable.com/acme/j/ABC123/apply"
    assert workable_apply_url("https://apply.workable.com/acme/j/ABC123/apply") == "https://apply.workable.com/acme/j/ABC123/apply"
    assert workable_apply_url("https://apply.workable.com/acme/j/ABC123?x=1") == "https://apply.workable.com/acme/j/ABC123/apply"
