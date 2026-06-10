"""TailorStudio — orchestrates the Writer↔Critic refine loop and returns the best result."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from tentacle_apply.config import settings
from tentacle_apply.tailor.critic import CriticAgent, Critique
from tentacle_apply.tailor.writer import WriterAgent


@dataclass
class TailorResult:
    resume: str
    cover_letter: str
    critique: Critique
    history: list[float] = field(default_factory=list)


class TailorStudio:
    def __init__(
        self,
        target: float | None = None,
        max_iters: int | None = None,
        patience: int | None = None,
        good_overall: float | None = None,
        good_grounding: float | None = None,
    ) -> None:
        self.target = settings.tailor_target if target is None else target
        self.max_iters = max(1, settings.tailor_max_iters if max_iters is None else max_iters)
        # Consecutive non-improving revisions tolerated before giving up (free models often plateau).
        self.patience = settings.tailor_patience if patience is None else patience
        # "Good enough" = will clear the orchestrator's quality gate; no point burning calls past it.
        self.good_overall = settings.min_critic_overall if good_overall is None else good_overall
        self.good_grounding = settings.min_grounding if good_grounding is None else good_grounding
        self.writer = WriterAgent()
        self.critic = CriticAgent()

    def _good_enough(self, crit: Critique) -> bool:
        return crit.overall >= self.good_overall and crit.scores.get("grounding", 0.0) >= self.good_grounding

    def run(self, job_text: str, resume_text: str, facts: str | None = None) -> TailorResult:
        facts = facts or resume_text

        # The cover letter only depends on (job, resume), not the refined draft, so generate it
        # concurrently with the resume Writer↔Critic loop — it overlaps ~one LLM round-trip off the
        # critical path. (complete() and the rate limiter are thread-safe.)
        with ThreadPoolExecutor(max_workers=1) as pool:
            cover_future = pool.submit(self.writer.cover_letter, job_text, resume_text)

            draft = self.writer.tailor_resume(job_text, resume_text)
            crit = self.critic.score(job_text, draft, facts)
            best_draft, best_crit = draft, crit
            history = [crit.overall]

            # Refine only while it's worth it: not yet at target, won't already pass the gate, under the
            # iteration cap, and revisions are still improving (patience guards plateaus/oscillation).
            iters = 1
            no_improve = 0
            while (
                iters < self.max_iters
                and best_crit.overall < self.target
                and not self._good_enough(best_crit)
            ):
                draft = self.writer.revise_resume(
                    job_text, resume_text, draft, crit.issues, crit.missing_keywords
                )
                crit = self.critic.score(job_text, draft, facts)
                history.append(crit.overall)
                if crit.overall > best_crit.overall + 0.5:
                    best_draft, best_crit = draft, crit
                    no_improve = 0
                else:
                    no_improve += 1
                    if no_improve > self.patience:
                        break
                iters += 1

            cover = cover_future.result()

        return TailorResult(
            resume=best_draft, cover_letter=cover, critique=best_crit, history=history
        )
