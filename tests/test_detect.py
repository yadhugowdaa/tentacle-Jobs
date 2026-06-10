"""Tier-0 ATS detector: network-free fingerprinting of URLs and page HTML."""

from tentacle_apply.discovery import detect
from tentacle_apply.discovery.registry import _domain_label, _looks_like_url


def test_detect_board_urls():
    assert detect.detect_in_text("https://boards.greenhouse.io/anthropic") == ("greenhouse", "anthropic")
    assert detect.detect_in_text("https://jobs.lever.co/netlify/abc") == ("lever", "netlify")
    assert detect.detect_in_text("https://jobs.ashbyhq.com/linear") == ("ashby", "linear")
    assert detect.detect_in_text("https://jobs.smartrecruiters.com/Visa") == ("smartrecruiters", "Visa")
    assert detect.detect_in_text("https://apply.workable.com/intercom") == ("workable", "intercom")


def test_detect_embedded_signatures_in_html():
    gh = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeco"></script>'
    assert detect.detect_in_text(gh) == ("greenhouse", "acmeco")
    lever = 'see <a href="https://jobs.lever.co/acme/123">roles</a>'
    assert detect.detect_in_text(lever) == ("lever", "acme")
    ashby = 'fetch("https://api.ashbyhq.com/posting-api/job-board/acme")'
    assert detect.detect_in_text(ashby) == ("ashby", "acme")
    wk = 'window.location="https://deel.workable.com/"'
    assert detect.detect_in_text(wk) == ("workable", "deel")


def test_detect_workday_urls():
    # CXS, locale-prefixed human, and bare board URLs all resolve to "{host}/{site}".
    assert detect.detect_in_text(
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA/Eng_JR1"
    ) == ("workday", "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
    assert detect.detect_in_text(
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs"
    ) == ("workday", "acme.wd1.myworkdayjobs.com/External")
    assert detect.detect_in_text(
        "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site"
    ) == ("workday", "salesforce.wd12.myworkdayjobs.com/External_Career_Site")


def test_workday_token_parse_roundtrip():
    from tentacle_apply.sources.workday import cxs_base, parse_token

    host, tenant, site = parse_token("nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
    assert (host, tenant, site) == ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite")
    assert cxs_base(host, tenant, site) == (
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite"
    )
    assert parse_token("not-a-workday-token") is None
    assert parse_token("acme.wd1.myworkdayjobs.com") is None  # missing site


def test_detect_ignores_reserved_tokens():
    # Infrastructure hosts/paths must never be mistaken for a company token.
    assert detect.detect_in_text("https://www.workable.com") is None
    assert detect.detect_in_text("just a sentence with no ats") is None
    assert detect.detect_in_text("") is None


def test_looks_like_url():
    assert _looks_like_url("https://acme.com/careers")
    assert _looks_like_url("careers.acme.com")
    assert _looks_like_url("acme.io")
    assert not _looks_like_url("Acme Corp")
    assert not _looks_like_url("openai")


def test_domain_label_strips_subdomains():
    assert _domain_label("https://careers.acme.com/jobs") == "acme"
    assert _domain_label("jobs.acme.io") == "acme"
    assert _domain_label("https://acme.com") == "acme"
    assert _domain_label("https://hiring.bigco.com") == "bigco"
