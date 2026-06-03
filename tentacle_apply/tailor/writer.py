"""WriterAgent — tailors a resume and writes a cover letter, grounded ONLY in real experience.

The strict grounding rules are the anti-hallucination guard: the model may rephrase, reorder, and
emphasize the candidate's genuine experience, but must never invent employers, titles, dates,
degrees, metrics, or skills. This protects the user from submitting false applications.
"""

from __future__ import annotations

from tentacle_apply.llm import complete

_GROUNDING = (
    "STRICT RULES:\n"
    "- Use ONLY facts present in CANDIDATE RESUME. Never invent employers, job titles, dates, "
    "degrees, certifications, metrics, or skills the candidate does not have.\n"
    "- You MAY rephrase, reorder, and emphasize real experience, and naturally surface job "
    "keywords the candidate genuinely matches.\n"
    "- Keep it truthful and ATS-friendly."
)

RESUME_PROMPT = (
    "You are an expert resume writer. Tailor the candidate's resume for the target job.\n\n"
    + _GROUNDING
    + "\nOutput clean Markdown only (no commentary).\n\n"
    "TARGET JOB:\n{job}\n\n"
    "CANDIDATE RESUME (ground truth — do not contradict or exceed):\n{resume}\n\n"
    "Write the tailored resume now."
)

REVISE_PROMPT = (
    "Improve this tailored resume by addressing the reviewer's feedback.\n\n"
    + _GROUNDING
    + "\nOutput the improved Markdown resume only.\n\n"
    "TARGET JOB:\n{job}\n\n"
    "CANDIDATE RESUME (ground truth):\n{resume}\n\n"
    "CURRENT DRAFT:\n{draft}\n\n"
    "REVIEWER FEEDBACK:\n- Issues: {issues}\n"
    "- Missing keywords (add ONLY if the candidate genuinely has them): {missing}"
)

COVER_PROMPT = (
    "Write a concise, specific cover letter (max ~220 words) for the candidate applying to the job. "
    "Ground every claim in the candidate's real experience; do not invent. Warm, professional, no "
    "clichés. Output the letter text only.\n\n"
    "TARGET JOB:\n{job}\n\n"
    "CANDIDATE RESUME (ground truth):\n{resume}"
)


class WriterAgent:
    TEMPERATURE = 0.6

    def tailor_resume(self, job_text: str, resume_text: str) -> str:
        prompt = RESUME_PROMPT.format(job=job_text[:2500], resume=resume_text[:4000])
        return complete(prompt, temperature=self.TEMPERATURE).strip()

    def revise_resume(
        self, job_text: str, resume_text: str, draft: str, issues: list[str], missing: list[str]
    ) -> str:
        prompt = REVISE_PROMPT.format(
            job=job_text[:2500],
            resume=resume_text[:4000],
            draft=draft[:4000],
            issues="; ".join(issues) or "none",
            missing=", ".join(missing) or "none",
        )
        return complete(prompt, temperature=self.TEMPERATURE).strip()

    def cover_letter(self, job_text: str, resume_text: str) -> str:
        prompt = COVER_PROMPT.format(job=job_text[:2500], resume=resume_text[:4000])
        return complete(prompt, temperature=self.TEMPERATURE).strip()
