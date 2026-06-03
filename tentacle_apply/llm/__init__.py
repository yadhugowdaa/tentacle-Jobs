"""LLM access layer — one provider-agnostic entry point with pool rotation + failover.

Ported from EngageAI (tentacle-x). `complete()` is the single call site used everywhere:
    - stable=False : round-robin the NVIDIA pool + fail over on rate-limit/error (throughput).
    - stable=True  : pin to one fixed model (reproducible judging / scoring).
    - temperature  : low for control/extraction, high for creative writing.
"""

from tentacle_apply.llm.provider import complete, make_llm

__all__ = ["complete", "make_llm"]
