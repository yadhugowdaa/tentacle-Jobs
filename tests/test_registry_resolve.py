"""Tests for the (network-free) parts of the company-registry resolver."""

from tentacle_apply.discovery.registry import _slugify_variants, parse_company_url


def test_parse_greenhouse_url():
    assert parse_company_url("https://boards.greenhouse.io/anthropic") == ("greenhouse", "anthropic")
    assert parse_company_url("https://job-boards.greenhouse.io/stripe/jobs/123") == ("greenhouse", "stripe")


def test_parse_lever_url():
    assert parse_company_url("https://jobs.lever.co/netlify/abc-123") == ("lever", "netlify")


def test_parse_ashby_url():
    # Ashby parsing works even though we don't fetch it yet (resolve_company gates on support).
    assert parse_company_url("https://jobs.ashbyhq.com/openai") == ("ashby", "openai")


def test_parse_unknown_url_is_none():
    assert parse_company_url("https://example.com/careers") is None
    assert parse_company_url("just some text") is None


def test_slugify_variants():
    assert _slugify_variants("Acme Corp") == ["acmecorp", "acme-corp"]
    assert _slugify_variants("Stripe") == ["stripe"]
    assert _slugify_variants("  Two  Words ") == ["twowords", "two-words"]
