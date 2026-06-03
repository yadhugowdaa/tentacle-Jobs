"""TailorStudio — orchestrates the Writer↔Critic refine loop and returns the best result."""

from __future__ import annotations

from dataclasses import dataclass, field

from tentacle_apply.tailor.critic import CriticAgent, Critique
from tentacle_apply.tailor.writer import WriterAgent


@dataclass
class TailorResult:
    resume: str
    cover_letter: str
    critique: Critique
    history: list[float] = field(default_factory=list)


class TailorStudio:
    def __init__(self, target: float = 80.0, max_iters: int = 3) -> None:
        self.target = target
        self.max_iters = max(1, max_iters)
        self.writer = WriterAgent()
        self.critic = CriticAgent()

    def run(self, job_text: str, resume_text: str, facts: str | None = None) -> TailorResult:
        facts = facts or resume_text

        draft = self.writer.tailor_resume(job_text, resume_text)
        crit = self.critic.score(job_text, draft, facts)
        best_draft, best_crit = draft, crit
        history = [crit.overall]

        iters = 1
        while best_crit.overall < self.target and iters < self.max_iters:
            draft = self.writer.revise_resume(
                job_text, resume_text, draft, crit.issues, crit.missing_keywords
            )
            crit = self.critic.score(job_text, draft, facts)
            history.append(crit.overall)
            if crit.overall > best_crit.overall:
                best_draft, best_crit = draft, crit
            iters += 1

        cover = self.writer.cover_letter(job_text, resume_text)
        return TailorResult(
            resume=best_draft, cover_letter=cover, critique=best_crit, history=history
        )
