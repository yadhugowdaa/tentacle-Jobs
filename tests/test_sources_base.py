"""Tests for source helpers: HTML stripping, query matching, date parsing, dedupe-on-store shape."""

from datetime import datetime

from tentacle_apply.sources.base import matches_query, parse_dt, strip_html


def test_strip_html_converts_breaks_and_unescapes():
    html = "<p>Hello&amp;World</p><br/>Line<div>two</div>"
    out = strip_html(html)
    assert "Hello&World" in out
    assert "Line" in out
    assert "<" not in out


def test_strip_html_empty():
    assert strip_html(None) == ""
    assert strip_html("") == ""


def test_matches_query_all_tokens_must_be_present():
    assert matches_query("python backend", "Senior Python Engineer", "backend services")
    assert not matches_query("python rust", "Senior Python Engineer", "backend services")


def test_matches_query_empty_query_matches_everything():
    assert matches_query("", "anything")


def test_parse_dt_iso_string():
    dt = parse_dt("2025-01-15T12:00:00Z")
    assert isinstance(dt, datetime)
    assert dt.year == 2025


def test_parse_dt_epoch_number():
    dt = parse_dt(1_700_000_000)
    assert isinstance(dt, datetime)


def test_parse_dt_bad_value_is_none():
    assert parse_dt(None) is None
    assert parse_dt("not-a-date") is None
