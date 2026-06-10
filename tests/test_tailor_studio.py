"""TailorStudio refine-loop control: early-exit so we don't burn LLM calls needlessly.

These use fake Writer/Critic agents (no LLM) to assert the loop stops at the right time:
  - stop as soon as the draft will clear the quality gate ("good enough"),
  - stop when revisions stop improving (plateau / patience),
  - never exceed max_iters.
"""

from tentacle_apply.tailor.critic import Critique
from tentacle_apply.tailor.studio import TailorStudio


class _FakeWriter:
    def __init__(self):
        self.tailor_calls = 0
        self.revise_calls = 0
        self.cover_calls = 0

    def tailor_resume(self, job, resume):
        self.tailor_calls += 1
        return "draft-0"

    def revise_resume(self, job, resume, draft, issues, missing):
        self.revise_calls += 1
        return f"draft-{self.revise_calls}"

    def cover_letter(self, job, resume):
        self.cover_calls += 1
        return "a cover letter"


class _FakeCritic:
    """Returns a scripted sequence of overall scores; grounding fixed high."""

    def __init__(self, overalls):
        self.overalls = list(overalls)
        self.calls = 0

    def score(self, job, resume, facts):
        overall = self.overalls[min(self.calls, len(self.overalls) - 1)]
        self.calls += 1
        return Critique(overall=overall, scores={"grounding": 95.0})


def _studio(overalls, **kw):
    s = TailorStudio(good_overall=70.0, good_grounding=80.0, **kw)
    s.writer = _FakeWriter()
    s.critic = _FakeCritic(overalls)
    return s


def test_exits_immediately_when_first_draft_passes_gate():
    # First draft already clears the gate (70/80) → no revisions, just tailor+critic(+cover).
    s = _studio([73.0], target=80.0, max_iters=3)
    res = s.run("job", "resume", "facts")
    assert s.writer.revise_calls == 0
    assert res.critique.overall == 73.0
    assert res.cover_letter == "a cover letter"


def test_plateau_stops_revising():
    # Below gate but revisions don't improve → patience=1 stops after the non-improving revision.
    s = _studio([60.0, 60.0, 60.0], target=80.0, max_iters=5, patience=1)
    res = s.run("job", "resume", "facts")
    # one non-improving revision tolerated, then stop (not all 4 revisions).
    assert s.writer.revise_calls == 2
    assert res.critique.overall == 60.0


def test_keeps_best_draft_across_iters():
    # Improves then dips; best (the high one) must be returned.
    s = _studio([55.0, 68.0, 64.0], target=80.0, max_iters=3, patience=0)
    res = s.run("job", "resume", "facts")
    assert res.critique.overall == 68.0


def test_never_exceeds_max_iters():
    s = _studio([10.0, 12.0, 14.0, 16.0, 18.0], target=80.0, max_iters=3, patience=9)
    s.run("job", "resume", "facts")
    # max_iters=3 → at most 2 revisions beyond the first draft.
    assert s.writer.revise_calls == 2
