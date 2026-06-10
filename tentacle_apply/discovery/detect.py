"""Tier-0 ATS detector: any company / careers URL → (ats, token).

Free, no LLM, no paid search. A "career page" is almost always a known ATS behind a custom domain,
so "point it at any company" reduces to recovering which ATS + which board token a page is wired to.

We try, cheapest first:
  1. **URL fingerprint** — the ATS board token is often right in the string the user pasted.
  2. **HTML fingerprint** — fetch the page (following redirects) and scan for embed scripts, board
     links, or API calls that reveal the ATS (e.g. `boards.greenhouse.io/embed/job_board?for=acme`,
     a `jobs.lever.co/acme` link, an `api.ashbyhq.com/posting-api/job-board/acme` call).

The caller is responsible for *verifying* the recovered token against the ATS public API before
trusting it — a fingerprint match is a strong hint, not proof.
"""

from __future__ import annotations

import re

import requests

from tentacle_apply.log import get_logger

log = get_logger(__name__)

_HTML_TIMEOUT = 15
_MAX_BYTES = 1_500_000  # only scan the first ~1.5 MB of HTML; embeds appear early

# A real browser UA: some careers pages 403 a bot UA before we ever see the embed.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# (ats, pattern) where group(1) is the board token. Ordered most-specific first; the same patterns
# match both a raw URL and occurrences embedded anywhere in a page's HTML (script/iframe/link/fetch).
FINGERPRINTS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"boards\.greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"api\.greenhouse\.io/v1/boards/([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)", re.I)),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/([\w-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w-]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"api\.smartrecruiters\.com/v1/companies/([\w-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("workable", re.compile(r"workable\.com/api/accounts/([\w-]+)", re.I)),
    ("workable", re.compile(r"\b([\w-]+)\.workable\.com", re.I)),
]

# Tokens that are infrastructure, not company slugs — never trust them as a board token.
_RESERVED = {
    "v0", "v1", "embed", "api", "www", "boards", "job-boards", "jobs", "careers",
    "apply", "job_board", "js", "help", "support", "blog", "developers", "static",
}


def _clean_token(token: str | None) -> str | None:
    token = (token or "").strip().strip("/")
    if not token or token.lower() in _RESERVED:
        return None
    return token


# Workday needs two pieces (host + career-site path), so it can't use the single-group machinery
# above. Matches CXS URLs, locale-prefixed human URLs, and bare board URLs:
#   nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/...
#   nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/...
#   nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite
_WORKDAY_RE = re.compile(
    r"([\w-]+\.wd\d+\.myworkdayjobs\.com)/(?:wday/cxs/[\w-]+/|(?:[a-z]{2}-[A-Z]{2}/)?)([\w-]+)",
    re.I,
)
_WORKDAY_SKIP = {"wday", "cxs", "job", "en", "en-us"}


def _detect_workday(text: str) -> tuple[str, str] | None:
    for m in _WORKDAY_RE.finditer(text):
        host, site = m.group(1), m.group(2)
        if site.lower() in _WORKDAY_SKIP:
            continue
        return "workday", f"{host}/{site}"
    return None


def detect_in_text(text: str) -> tuple[str, str] | None:
    """Return the first (ats, token) fingerprinted in `text` (a URL or page HTML), else None."""
    if not text:
        return None
    for ats, pat in FINGERPRINTS:
        for m in pat.finditer(text):
            token = _clean_token(m.group(1))
            if token:
                return ats, token
    return _detect_workday(text)


def detect_ats(url: str) -> tuple[str, str] | None:
    """Best-effort (ats, token) for any company or careers URL. None if undetectable."""
    if not url:
        return None

    # 1) Token straight out of the pasted string (board URL or obvious embed host).
    hit = detect_in_text(url)
    if hit:
        return hit

    fetch_url = url if re.match(r"^https?://", url, re.I) else f"https://{url}"
    try:
        resp = requests.get(
            fetch_url,
            headers={"User-Agent": _BROWSER_UA, "Accept": "text/html,application/xhtml+xml,*/*"},
            timeout=_HTML_TIMEOUT,
            allow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001 - a failed fetch just means "couldn't detect"
        log.info("detect: fetch failed for %s: %s", fetch_url, str(exc)[:120])
        return None

    # 2a) A redirect may land us directly on the ATS board host.
    hit = detect_in_text(str(resp.url))
    if hit:
        log.info("detect: %s -> %s via redirect %s", url, hit, resp.url)
        return hit

    # 2b) Fingerprint the page body (embed scripts, board links, API calls).
    html = (resp.text or "")[:_MAX_BYTES]
    hit = detect_in_text(html)
    if hit:
        log.info("detect: %s -> %s via HTML fingerprint", url, hit)
    return hit
