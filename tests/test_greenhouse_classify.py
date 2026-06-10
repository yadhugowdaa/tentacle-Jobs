"""Greenhouse appliability classification: only `*.greenhouse.io` forms are Tier-1 applyable."""

from tentacle_apply.sources.greenhouse import ats_type_for_url


def test_greenhouse_hosted_urls_are_applyable():
    assert ats_type_for_url("https://job-boards.greenhouse.io/anthropic/jobs/123") == "greenhouse"
    assert ats_type_for_url("https://boards.greenhouse.io/figma/jobs/456?gh_jid=456") == "greenhouse"


def test_company_hosted_urls_are_external():
    assert ats_type_for_url("https://stripe.com/jobs/search?gh_jid=7964697") == "greenhouse_external"
    assert ats_type_for_url("https://databricks.com/company/careers/job?gh_jid=1") == "greenhouse_external"
    assert ats_type_for_url("https://www.brex.com/careers/123?gh_jid=123") == "greenhouse_external"


def test_empty_url_is_external_not_crash():
    assert ats_type_for_url("") == "greenhouse_external"
    assert ats_type_for_url(None) == "greenhouse_external"
