"""CriticAgent — strict, deterministic scoring of a tailored resume against the job.

Uses the stable model at temperature 0 so scores are comparable across iterations. `grounding` is
the key reliability signal: it drops if the resume looks like it invented anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tentacle_apply.llm import complete
from tentacle_apply.structured import parse_json

WEIGHTS = {"relevance": 0.35, "keyword_coverage": 0.25, "grounding": 0.25, "clarity": 0.15}

CRITIC_PROMPT = (
    "You are a strict hiring reviewer. Score the TAILORED RESUME against the JOB.\n"
    "Return ONLY JSON with this shape:\n"
    '{{"relevance":0,"keyword_coverage":0,"grounding":0,"clarity":0,'
    '"missing_keywords":[],"issues":[]}}\n'
    "- relevance (0-100): how well the resume targets THIS job.\n"
    "- keyword_coverage (0-100): important JD skills/keywords present in the resume.\n"
    "- grounding (0-100): 100 if every claim is supported by CANDIDATE FACTS; lower if anything "
    "looks invented or exaggerated.\n"
    "- clarity (0-100): structure, readability, ATS-friendliness.\n"
    "- missing_keywords: JD keywords the candidate plausibly has but the resume omits.\n"
    "- issues: concrete, fixable problems.\n\n"
    "JOB:\n{job}\n\n"
    "CANDIDATE FACTS (ground truth):\n{facts}\n\n"
    "TAILORED RESUME:\n{resume}"
)


def _num(value) -> float:
    try:
        return max(0.0, min(100.0, float(re.sub(r"[^0-9.]", "", str(value)) or 0)))
    except ValueError:
        return 0.0


def _str_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [s.strip() for s in re.split(r"[,;]", value) if s.strip()]
    return []


@dataclass
class Critique:
    overall: float
    scores: dict[str, float] = field(default_factory=dict)
    missing_keywords: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


class CriticAgent:
    def score(self, job_text: str, resume: str, facts: str) -> Critique:
        prompt = CRITIC_PROMPT.format(job=job_text[:2500], facts=facts[:3000], resume=resume[:4000])
        data = parse_json(complete(prompt, stable=True, temperature=0.0))
        scores = {k: _num(data.get(k, 0)) for k in WEIGHTS}
        overall = round(sum(scores[k] * w for k, w in WEIGHTS.items()), 1)
        return Critique(
            overall=overall,
            scores=scores,
            missing_keywords=_str_list(data.get("missing_keywords")),
            issues=_str_list(data.get("issues")),
        )
