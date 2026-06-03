"""Provider-agnostic LLM calls with a rotation pool, failover, and rate limiting.

Free-tier reality: each key has a per-minute cap and the hosted clients don't auto-retry. So we
(1) round-robin across a pool of (key, model) endpoints, (2) fail over instantly to the next
endpoint on any error, and (3) back off with jitter only when EVERY endpoint is rate-limited.
"""

from __future__ import annotations

import itertools
import threading

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.rate_limiters import InMemoryRateLimiter
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from tentacle_apply.config import NvidiaEndpoint, settings
from tentacle_apply.log import get_logger

log = get_logger(__name__)

# Shared limiter so all calls together respect an aggregate budget. With a pool, each key sees rate/N.
_RATE_LIMITER = InMemoryRateLimiter(
    requests_per_second=settings.llm_rps, check_every_n_seconds=0.5, max_bucket_size=1
)

_rr_lock = threading.Lock()
_rr_counter = itertools.count()


def _is_rate_limit(exc: BaseException) -> bool:
    msg = str(exc)
    return "429" in msg or "Too Many Requests" in msg or "RESOURCE_EXHAUSTED" in msg


def _nvidia_endpoints() -> list[NvidiaEndpoint]:
    if settings.nvidia_pool:
        return list(settings.nvidia_pool)
    return [NvidiaEndpoint(key=settings.nvidia_api_key, model=settings.nvidia_model)]


def _build_nvidia(ep: NvidiaEndpoint, temperature: float = 0.2) -> BaseChatModel:
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    extra = {}
    if "deepseek" in ep.model:  # only deepseek accepts the thinking-disable flag
        extra["model_kwargs"] = {"extra_body": {"chat_template_kwargs": {"thinking": False}}}
    return ChatNVIDIA(
        model=ep.model, api_key=ep.key, temperature=temperature, rate_limiter=_RATE_LIMITER, **extra
    )


def _build_gemini(temperature: float = 0.2) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=settings.llm_model,
        google_api_key=settings.gemini_api_key,
        temperature=temperature,
        max_retries=4,
        rate_limiter=_RATE_LIMITER,
    )


def make_llm() -> BaseChatModel:
    """Return ONE chat model, rotating through the pool (for callers wanting a single model)."""
    if settings.llm_provider == "nvidia":
        eps = _nvidia_endpoints()
        with _rr_lock:
            idx = next(_rr_counter)
        return _build_nvidia(eps[idx % len(eps)])
    return _build_gemini()


def _ordered_endpoints() -> list[NvidiaEndpoint | None]:
    """Endpoints to try for ONE logical call, rotated. `None` = the Gemini single endpoint."""
    if settings.llm_provider != "nvidia":
        return [None]
    eps = _nvidia_endpoints()
    n = len(eps)
    with _rr_lock:
        start = next(_rr_counter)
    return [eps[(start + i) % n] for i in range(n)]


@retry(
    retry=retry_if_exception(_is_rate_limit),
    wait=wait_exponential(multiplier=4, min=4, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _invoke(payload, temperature: float = 0.2) -> str:
    """Invoke with pool rotation + failover. `payload` is a str or a list of messages."""
    endpoints = _ordered_endpoints()
    last_exc: BaseException | None = None
    for ep in endpoints:
        llm = _build_gemini(temperature) if ep is None else _build_nvidia(ep, temperature)
        try:
            return llm.invoke(payload).content
        except Exception as exc:  # noqa: BLE001 - fail over to the next endpoint
            last_exc = exc
            model = "gemini" if ep is None else ep.model
            log.warning("LLM endpoint failed (%s): %s — failing over", model, str(exc)[:120])
            if len(endpoints) == 1 and not _is_rate_limit(exc):
                raise
            continue
    assert last_exc is not None
    raise last_exc


@retry(
    retry=retry_if_exception(_is_rate_limit),
    wait=wait_exponential(multiplier=4, min=4, max=60),
    stop=stop_after_attempt(6),
    reraise=True,
)
def _invoke_stable(payload, temperature: float = 0.2) -> str:
    """Always one fixed model — for scoring/judging where comparable outputs matter."""
    if settings.llm_provider == "nvidia":
        ep = NvidiaEndpoint(key=settings.nvidia_api_key or _nvidia_endpoints()[0].key,
                            model=settings.nvidia_model)
        llm = _build_nvidia(ep, temperature)
    else:
        llm = _build_gemini(temperature)
    return llm.invoke(payload).content


def complete(prompt: str, stable: bool = False, temperature: float = 0.2) -> str:
    """Provider-agnostic single-prompt completion (see module/llm __init__ for the modes)."""
    if stable:
        return _invoke_stable(prompt, temperature)
    return _invoke(prompt, temperature)
