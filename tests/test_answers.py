"""Tests for deterministic screening-answer logic (the parts that must never call an LLM).

Free-text answers do call the LLM, so we only test the deterministic branches here.
"""

from tentacle_apply.apply.answers import (
    _is_yes_no,
    _match_country,
    _needs_sponsorship,
    answer_choice,
    answer_text,
)
from tentacle_apply.apply.base import Applicant


def _applicant(**kw) -> Applicant:
    base = dict(first_name="Ada", last_name="Lovelace", email="ada@x.com")
    base.update(kw)
    return Applicant(**base)


def test_eeo_question_declines():
    choice = answer_choice(
        "Gender", ["Male", "Female", "Decline to self-identify"], _applicant()
    )
    assert choice == "Decline to self-identify"


def test_eeo_falls_back_to_last_option_when_no_decline():
    choice = answer_choice("Race / Ethnicity", ["White", "Asian", "Other"], _applicant())
    assert choice == "Other"


def test_work_authorization_yes_no_answers_yes():
    choice = answer_choice("Are you legally authorized to work?", ["Yes", "No"], _applicant())
    assert choice == "Yes"


def test_work_authorization_nuanced_list_left_blank():
    # Not a clean yes/no → we must NOT guess.
    choice = answer_choice(
        "Authorization status", ["Citizen", "Visa holder", "Need sponsorship"], _applicant()
    )
    assert choice is None


def test_sponsorship_no_when_not_needed():
    choice = answer_choice("Do you require visa sponsorship?", ["Yes", "No"], _applicant(work_auth="US Citizen"))
    assert choice == "No"


def test_sponsorship_yes_when_needed():
    choice = answer_choice(
        "Do you require visa sponsorship?", ["Yes", "No"], _applicant(work_auth="Will require H1B sponsorship")
    )
    assert choice == "Yes"


def test_is_yes_no_detection():
    assert _is_yes_no(["yes", "no"])
    assert not _is_yes_no(["yes", "no", "maybe"])
    assert not _is_yes_no([])


def test_needs_sponsorship_keywords():
    assert _needs_sponsorship(_applicant(work_auth="needs visa, h1b"))
    assert not _needs_sponsorship(_applicant(work_auth="permanent resident"))
    assert not _needs_sponsorship(_applicant(work_auth=""))


def test_match_country_strips_phone_code():
    opts = ["United States +1", "India +91", "United Kingdom +44"]
    chosen = _match_country(opts, [o.lower() for o in opts], _applicant(location="Bangalore, India"))
    assert chosen == "India +91"


def test_answer_text_salary():
    assert answer_text("Expected salary", _applicant(min_salary=120000), "") == "120000"
    assert "Negotiable" in answer_text("Compensation expectation", _applicant(), "")


def test_answer_text_links():
    a = _applicant(links={"linkedin": "https://linkedin.com/in/ada", "github": "https://github.com/ada"})
    assert answer_text("LinkedIn profile URL", a, "") == "https://linkedin.com/in/ada"
    assert answer_text("GitHub", a, "") == "https://github.com/ada"


def test_answer_text_years():
    assert answer_text("How many years of experience", _applicant(years_exp=8.0), "") == "8"
