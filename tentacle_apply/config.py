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

    # --- Matching / embeddings (local, free) ---
    embed_model: str = "BAAI/bge-small-en-v1.5"

    # --- Defaults ---
    default_target_applications: int = 20

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
