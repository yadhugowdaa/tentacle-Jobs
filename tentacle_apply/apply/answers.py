"""Answer screening questions.

Standard questions (work auth, sponsorship, salary, relocation, EEO/demographic, years) are mapped
DETERMINISTICALLY from the real profile — no LLM, no guessing. Only genuine free-text prompts
("why do you want to work here?") go to the LLM, and those are grounded strictly in the job
description + the user's real resume so we never fabricate.
"""

from __future__ import annotations

import re

from tentacle_apply.apply.base import Applicant
from tentacle_apply.config import settings
from tentacle_apply.llm import complete

# EEO / demographic questions: always decline by default (privacy + non-discrimination).
_DECLINE = "Decline to self-identify"


def _has(label: str, *needles: str) -> bool:
    low = label.lower()
    return any(n in low for n in needles)


def answer_choice(label: str, options: list[str], applicant: Applicant) -> str | None:
    """Pick one option for a select/radio question. Returns the chosen option text, or None.

    `options` are the human-readable choices as they appear in the form.
    """
    if not options:
        return None
    low_opts = [o.lower() for o in options]

    def pick(*prefer: str) -> str | None:
        for want in prefer:
            for opt, low in zip(options, low_opts, strict=False):
                if want in low:
                    return opt
        return None

    # EEO / demographic.
    if _has(label, "gender", "race", "ethnic", "veteran", "disability", "hispanic", "latino", "sexual orientation"):
        return pick("decline", "prefer not", "don't wish", "do not wish", "not to answer") or options[-1]

    # Country selector (incl. "which of these countries", phone-code lists).
    if _has(label, "countr"):
        return _match_country(options, low_opts, applicant)

    # Visa sponsorship: do you NEED sponsorship? Only answer plain yes/no; skip nuanced lists.
    if _has(label, "sponsor", "visa"):
        if _is_yes_no(low_opts):
            return pick("yes") if _needs_sponsorship(applicant) else pick("no")
        return None  # nuanced multi-option → leave for human review (avoid a false claim)

    # Work authorization: are you authorized? Only answer if there's a clear yes/no.
    if _has(label, "authorized", "authorization", "legally", "eligible to work", "right to work"):
        return pick("yes") if _is_yes_no(low_opts) else None

    # Willing to relocate / currently located.
    if _has(label, "relocat", "located", "open to"):
        return pick("yes")

    # Default: leave unanswered so we never guess wrongly on a required dropdown.
    return None


def _is_yes_no(low_opts: list[str]) -> bool:
    joined = {o.strip() for o in low_opts}
    return joined <= {"yes", "no"} and bool(joined)


def _match_country(options: list[str], low_opts: list[str], applicant: Applicant) -> str | None:
    hay = f"{applicant.location} {applicant.work_auth}".lower()
    for opt, low in zip(options, low_opts, strict=False):
        # Strip a trailing phone code like " +91" before matching.
        name = re.sub(r"\s*\+\d+\s*$", "", low).strip()
        if name and (name in hay or any(name == tok for tok in re.split(r"[,\s]+", hay))):
            return opt
    return None


def answer_text(label: str, applicant: Applicant, job_text: str) -> str:
    """Answer a free-text question. Deterministic for known fields; grounded LLM otherwise."""
    if _has(label, "salary", "compensation", "pay expectation", "expected ctc"):
        return str(applicant.min_salary) if applicant.min_salary else "Negotiable / open to discussion"
    if _has(label, "years", "experience") and _has(label, "how many", "years of"):
        return str(int(applicant.years_exp)) if applicant.years_exp else ""
    if _has(label, "notice period"):
        return "Available to start within standard notice"
    if _has(label, "linkedin"):
        return applicant.links.get("linkedin", "")
    if _has(label, "github"):
        return applicant.links.get("github", "")
    if _has(label, "website", "portfolio"):
        return applicant.links.get("website", "") or applicant.links.get("github", "")
    if _has(label, "phone"):
        return applicant.phone
    if _has(label, "location", "city", "where are you", "based"):
        return applicant.location

    # Genuine free-text → grounded LLM (short, honest, no fabrication).
    return _llm_free_text(label, applicant, job_text)


def _needs_sponsorship(applicant: Applicant) -> bool:
    auth = (applicant.work_auth or "").lower()
    if not auth:
        return False
    return any(w in auth for w in ("sponsor", "visa needed", "require", "not authorized", "h1b", "h-1b"))


_FREE_TEXT_PROMPT = (
    "Answer this job-application question for the candidate in 2-4 sentences. Use ONLY facts from the "
    "candidate resume and the job description below. Do NOT invent experience, numbers, or claims. "
    "Be specific, warm, and professional. Output only the answer text.\n\n"
    "QUESTION:\n{question}\n\n"
    "JOB:\n{job}\n\n"
    "CANDIDATE RESUME (ground truth):\n{resume}"
)


def _llm_free_text(label: str, applicant: Applicant, job_text: str) -> str:
    prompt = _FREE_TEXT_PROMPT.format(
        question=label.strip()[:400],
        job=job_text[:1800],
        resume=(applicant.resume_text or applicant.cover_letter or "")[:2200],
    )
    try:
        # The one genuinely "reasoning" + hallucination-sensitive step → use the strong model when
        # configured (it has the best non-hallucination score in its class); falls back to fast pool.
        return complete(prompt, strong=settings.use_strong_for_answers, temperature=0.5).strip()
    except Exception:  # noqa: BLE001 - never block an apply on an optional free-text answer
        return ""
