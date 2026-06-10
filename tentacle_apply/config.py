"""Central, typed configuration for tentacle-apply.

Everything is overridable via a .env file (see .env.example). Free defaults: the app runs on
NVIDIA NIM / Gemini free tiers and a local SQLite database with zero paid services.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = the folder containing this package (…/tentacle-apply).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RESUMES_DIR = DATA_DIR / "resumes"


class NvidiaEndpoint(BaseModel):
    """One (api_key, model) pair in the NVIDIA rotation pool."""

    key: str
    model: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM provider ---
    llm_provider: str = "nvidia"  # "nvidia" | "gemini"

    gemini_api_key: str = ""
    llm_model: str = "gemini-2.5-flash-lite"

    nvidia_api_key: str = ""
    nvidia_model: str = "meta/llama-3.3-70b-instruct"
    nvidia_pool: list[NvidiaEndpoint] = []

    # Optional "strong" reasoning model for the hard, hallucination-sensitive step (free-text
    # screening answers) — never the workhorse. Nemotron-3 Ultra has the best non-hallucination
    # score in its class, which is what matters for grounded answers. Pre-configured but OPT-IN:
    # the *free* NIM endpoint for this 550B model is heavily queued (measured >4 min for a trivial
    # prompt), so it's off by default. Enable `use_strong_for_answers` once you have a faster
    # (partner/paid) endpoint. NVIDIA provider only; leave `strong_model` empty to hard-disable.
    strong_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    # Thinking budget for the strong model (0 = thinking off, fastest). Small keeps it usable; we
    # discard the raw reasoning trace and keep only the final answer.
    strong_reasoning_budget: int = 2048
    # Hard wall-clock cap on a strong call; on timeout we fall back to the fast pool so a slow/queued
    # endpoint can never stall an apply.
    strong_timeout_s: int = 45
    # Route free-text screening answers through the strong model (falls back to the fast pool on
    # error/timeout). Default off because the free endpoint is too slow — see note above.
    use_strong_for_answers: bool = False

    llm_rps: float = 1.0

    # --- Database ---
    # Empty => local SQLite at data/tentacle_apply.db. Set a Postgres URL to host.
    database_url: str = ""

    # --- Job sources ---
    # Adzuna (free key at https://developer.adzuna.com). Skipped if empty.
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_country: str = "in"  # 2-letter country code Adzuna searches (gb, us, in, …)
    # Public ATS boards to pull from (these are also our auto-apply targets).
    greenhouse_companies: list[str] = ["anthropic"]
    lever_companies: list[str] = ["lever"]
    ashby_companies: list[str] = ["ashby"]
    workable_companies: list[str] = ["careers"]
    smartrecruiters_companies: list[str] = ["Square"]
    # Workday tokens are "{host}/{site}", e.g. "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite".
    workday_companies: list[str] = ["nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"]

    # --- Matching / embeddings (local, free) ---
    embed_model: str = "BAAI/bge-small-en-v1.5"

    # --- Defaults ---
    default_target_applications: int = 20

    # --- Autonomous run loop (Phase B) ---
    # Default submit behaviour for a run: "prepare" (fill + screenshot, no submit — the safe,
    # hosted/multi-user default), "submit" (auto, skips CAPTCHA), "hitl" (local; you solve CAPTCHA).
    run_mode: str = "prepare"
    # Quality gate: a job is only prepared/submitted when it clears ALL of these.
    min_match_score: float = 55.0      # embedding+overlap score, 0–100
    min_critic_overall: float = 70.0   # CriticAgent weighted score, 0–100
    min_grounding: float = 80.0        # anti-hallucination floor, 0–100
    # Ranking: multiply a job's score by this when its posting language doesn't match the resume's
    # (e.g. a German posting for an English resume). Down-ranks rather than discards. 1.0 disables.
    lang_mismatch_penalty: float = 0.5
    # Tailoring (Writer↔Critic) speed/quality knobs. The loop early-exits once the draft will clear
    # the quality gate (min_critic_overall + min_grounding), reaches `tailor_target`, hits the
    # `tailor_max_iters` cap, or stops improving for `tailor_patience` revisions (free models plateau).
    tailor_target: float = 80.0
    tailor_max_iters: int = 3
    tailor_patience: int = 1
    # Safety cap: most candidates a single run will tailor/attempt before giving up on the pool.
    run_max_candidates: int = 60
    # Refresh the job pool (discovery) at the start of each run.
    run_discover: bool = True

    # --- Logging ---
    log_level: str = "INFO"  # DEBUG | INFO | WARNING | ERROR

    @property
    def llm_key(self) -> str:
        """A usable credential for the selected provider (or '' if none)."""
        if self.llm_provider == "nvidia":
            return self.nvidia_api_key or (self.nvidia_pool[0].key if self.nvidia_pool else "")
        return self.gemini_api_key

    @property
    def db_url(self) -> str:
        """SQLAlchemy URL. Defaults to local SQLite; override via DATABASE_URL for Postgres."""
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(DATA_DIR / 'tentacle_apply.db').as_posix()}"


settings = Settings()
