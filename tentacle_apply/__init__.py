"""tentacle-apply — a reliability-first, autonomous job-application co-pilot (Octopodia 🐙)."""

from __future__ import annotations

import sys
import warnings

__version__ = "0.0.1"

# Windows consoles default to cp1252 and choke on Unicode (emoji, smart quotes). Force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

warnings.filterwarnings("ignore", category=UserWarning, module="langchain_nvidia_ai_endpoints")
