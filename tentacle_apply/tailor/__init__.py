"""Tailor: RAG-grounded resume + cover-letter generation with a Writer↔Critic refine loop."""

from tentacle_apply.tailor.critic import CriticAgent, Critique
from tentacle_apply.tailor.studio import TailorResult, TailorStudio
from tentacle_apply.tailor.writer import WriterAgent

__all__ = ["WriterAgent", "CriticAgent", "Critique", "TailorStudio", "TailorResult"]
