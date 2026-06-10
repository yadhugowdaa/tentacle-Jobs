"""Tests for the matcher's scoring + eligibility primitives (no embeddings/network needed)."""

import numpy as np

from tentacle_apply.matching.matcher import (
    _cosine,
    _eligible,
    _english_ratio,
    _language_penalty,
    _skill_overlap,
)


def test_skill_overlap_fraction():
    skills = ["python", "rust", "go", "sql"]
    text = "we use python and sql daily"
    assert _skill_overlap(skills, text) == 0.5


def test_skill_overlap_no_skills():
    assert _skill_overlap([], "anything") == 0.0


def test_eligible_no_preferences_allows_all():
    ok, reason = _eligible([], "Mars")
    assert ok and reason == ""


def test_eligible_remote_always_ok():
    ok, _ = _eligible(["London"], "Remote - Worldwide")
    assert ok


def test_eligible_matching_location():
    ok, _ = _eligible(["Bangalore", "London"], "London, UK")
    assert ok


def test_eligible_unknown_location_not_excluded():
    ok, _ = _eligible(["London"], "")
    assert ok


def test_eligible_mismatch_is_flagged():
    ok, reason = _eligible(["London"], "Tokyo, Japan")
    assert not ok
    assert "Tokyo" in reason


_EN = "We are looking for a senior software engineer to build and ship our backend services with you."
_DE = (
    "Wir suchen einen erfahrenen Softwareentwickler für unser wachsendes Team. Sie entwickeln und "
    "betreuen unsere Backend-Dienste und arbeiten eng mit dem Produktteam zusammen. Erfahrung mit "
    "Python und relationalen Datenbanken wird vorausgesetzt. Wir bieten flexible Arbeitszeiten."
)


def test_english_ratio_separates_languages():
    assert _english_ratio(_EN) > 0.15
    assert _english_ratio(_DE) < 0.09


def test_language_penalty_downranks_non_english_for_english_resume():
    # English resume vs a German posting → penalized (<1.0).
    assert _language_penalty(_EN, _DE) < 1.0
    # English resume vs English posting → no penalty.
    assert _language_penalty(_EN, _EN) == 1.0


def test_language_penalty_skips_when_resume_not_english():
    # A German resume shouldn't penalize German postings.
    assert _language_penalty(_DE, _DE) == 1.0


def test_cosine_identical_vectors_score_one():
    q = np.array([1.0, 0.0, 0.0], dtype="float32")
    m = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype="float32")
    sims = _cosine(q, m)
    assert sims[0] == 1.0
    assert abs(sims[1]) < 1e-6
