"""Tests for the defensive coercion that turns messy LLM profile output into a clean ProfileData."""

from tentacle_apply.intake.profile import _as_float, _as_list, _as_salary, _coerce


def test_as_list_from_list():
    assert _as_list(["Python", " Rust ", ""]) == ["Python", "Rust"]


def test_as_list_from_delimited_string():
    assert _as_list("Python, Rust; Go") == ["Python", "Rust", "Go"]


def test_as_list_from_garbage():
    assert _as_list(None) == []
    assert _as_list(42) == []


def test_as_float_handles_text():
    assert _as_float("5 years") == 5.0
    assert _as_float("3.5") == 3.5
    assert _as_float(None) == 0.0


def test_as_salary_with_k_suffix():
    assert _as_salary("120k") == 120000
    assert _as_salary("$95,000") == 95000
    assert _as_salary(80000) == 80000


def test_as_salary_empty_is_none():
    assert _as_salary(None) is None
    assert _as_salary("") is None
    assert _as_salary("negotiable") is None


def test_coerce_full_object():
    data = {
        "full_name": "  Ada Lovelace ",
        "skills": "Python, Math",
        "years_exp": "7 yrs",
        "titles": ["Engineer"],
        "locations": "London",
        "work_auth": "Citizen",
        "min_salary": "100k",
        "summary": "Pioneer.",
    }
    p = _coerce(data)
    assert p.full_name == "Ada Lovelace"
    assert p.skills == ["Python", "Math"]
    assert p.years_exp == 7.0
    assert p.titles == ["Engineer"]
    assert p.locations == ["London"]
    assert p.min_salary == 100000


def test_coerce_empty_object_has_safe_defaults():
    p = _coerce({})
    assert p.full_name == ""
    assert p.skills == []
    assert p.years_exp == 0.0
    assert p.min_salary is None
