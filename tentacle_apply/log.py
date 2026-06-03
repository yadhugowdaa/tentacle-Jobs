"""Minimal, dependency-free structured logging.

One configured root for the whole package. Level comes from the `LOG_LEVEL` env var (default
INFO). Call `get_logger(__name__)` anywhere; the first call configures handlers idempotently so
importing this module never double-adds handlers (e.g. under uvicorn reloads or pytest).
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"


def _default_level() -> str:
    """Prefer the real env var, then the project's .env-backed setting, else INFO."""
    if os.getenv("LOG_LEVEL"):
        return os.environ["LOG_LEVEL"]
    try:
        from tentacle_apply.config import settings

        return settings.log_level
    except Exception:  # noqa: BLE001 - logging must never fail to import
        return "INFO"


def configure_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    resolved = (level or _default_level() or "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger("tentacle_apply")
    root.setLevel(getattr(logging, resolved, logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a package-scoped logger, configuring handlers on first use."""
    configure_logging()
    # Normalize "tentacle_apply.sources.greenhouse" → keep as-is; bare names get namespaced.
    if not name.startswith("tentacle_apply"):
        name = f"tentacle_apply.{name}"
    return logging.getLogger(name)
