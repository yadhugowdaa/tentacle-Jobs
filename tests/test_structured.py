"""Tests for robust JSON extraction from messy LLM output."""

from tentacle_apply.structured import parse_json


def test_plain_json():
    assert parse_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_code_fenced_json():
    text = '```json\n{"a": 1}\n```'
    assert parse_json(text) == {"a": 1}


def test_strips_think_block():
    text = '<think>let me reason...</think>\n{"ok": true}'
    assert parse_json(text) == {"ok": True}


def test_picks_largest_balanced_object_amid_prose():
    text = 'Sure! Here is your data: {"name": "Ada", "skills": ["py", "rust"]} hope that helps'
    assert parse_json(text) == {"name": "Ada", "skills": ["py", "rust"]}


def test_braces_inside_strings_do_not_break_parsing():
    text = '{"note": "use {curly} braces", "n": 2}'
    assert parse_json(text) == {"note": "use {curly} braces", "n": 2}


def test_garbage_returns_empty_dict():
    assert parse_json("no json here at all") == {}
    assert parse_json("") == {}


def test_top_level_array_is_not_a_dict():
    # parse_json only returns dicts; a bare array should yield {}.
    assert parse_json("[1, 2, 3]") == {}
