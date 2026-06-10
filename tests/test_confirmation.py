"""Submission-verification logic: a confirmation must be corroborated, never guessed from a URL.

`_decide_confirmation` is the pure core of `page_confirms` (no Playwright), so we can exhaustively
check the false-positive guards that protect us from recording a fake "verified" submission.
"""

from tentacle_apply.apply._common import _decide_confirmation

_APPLY_URL = "https://boards.greenhouse.io/acme/jobs/123/apply"
_THANKS_URL = "https://boards.greenhouse.io/acme/confirmation"


def test_confirm_phrase_is_trusted():
    assert _decide_confirmation(_APPLY_URL, "Thank you for applying!", False, False, False) == _APPLY_URL


def test_confirm_element_is_trusted():
    assert _decide_confirmation(_APPLY_URL, "all done", True, False, False) == _APPLY_URL


def test_confirm_phrase_rejected_when_form_still_has_errors():
    # The word may appear in boilerplate while the form is still up with a validation error.
    assert _decide_confirmation(_APPLY_URL, "application submitted? please complete email", False, True, True) is None


def test_url_hint_alone_is_not_enough():
    # A confirm-y URL while the form is still present must NOT count as submitted.
    assert _decide_confirmation(_THANKS_URL, "fill in your details", False, True, False) is None


def test_url_hint_counts_when_form_gone_and_no_error():
    assert _decide_confirmation(_THANKS_URL, "you're all set", False, False, False) == _THANKS_URL


def test_url_hint_rejected_when_error_visible():
    assert _decide_confirmation(_THANKS_URL, "there was a problem", False, False, True) is None


def test_no_signal_returns_none():
    assert _decide_confirmation(_APPLY_URL, "just a normal job description", False, True, False) is None
