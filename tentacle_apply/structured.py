"""Robust JSON extraction from LLM output.

Models wrap JSON in reasoning preambles (<think>…</think>), code fences, or stray prose. This
strips that noise and pulls the largest balanced JSON object so structured parsing rarely fails.
"""

from __future__ import annotations

import json
import re


def _balanced_objects(s: str) -> list[str]:
    """Return every balanced {...} span via brace-depth scanning (ignores braces in strings)."""
    spans: list[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    spans.append(s[start : i + 1])
    return spans


def parse_json(text: str) -> dict:
    """Best-effort parse of a JSON object from arbitrary LLM text. Returns {} on failure."""
    if not text:
        return {}
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        pass
    for span in sorted(_balanced_objects(cleaned), key=len, reverse=True):
        try:
            result = json.loads(span)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue
    return {}
