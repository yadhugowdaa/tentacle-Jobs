"""Shared browser helpers for Tier-1 ATS templates (Greenhouse/Lever/Ashby).

Deterministic, selector-based utilities used by every applier: label extraction, CAPTCHA
detection, required-field detection, generic field fill, and submit+verify. Kept here so each ATS
template stays small and they don't drift apart.
"""

from __future__ import annotations

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# JS that walks the DOM to find a human-readable label for a form control.
LABEL_JS = """
(el) => {
  const txt = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  if (el.id) { const l = document.querySelector('label[for=\"' + el.id + '\"]'); if (l) return txt(l.innerText); }
  let p = el.closest('label'); if (p) return txt(p.innerText);
  if (el.getAttribute('aria-label')) return txt(el.getAttribute('aria-label'));
  const lb = el.getAttribute('aria-labelledby'); if (lb) { const e = document.getElementById(lb); if (e) return txt(e.innerText); }
  let c = el.closest('div, li, fieldset');
  for (let k = 0; k < 6 && c; k++) { const l = c.querySelector('label, legend, .application-label'); if (l) return txt(l.innerText); c = c.parentElement; }
  return txt(el.name || '');
}
"""

# JS that lists labels of required, still-empty inputs the user must complete.
MISSING_JS = """
() => {
  const out = [];
  const txt = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('input, textarea, select').forEach((el) => {
    const req = el.required || el.getAttribute('aria-required') === 'true';
    if (!req) return;
    const t = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'file'].includes(t)) return;
    if (t === 'radio' || t === 'checkbox') {
      const name = el.name;
      if (name && document.querySelector('input[name=\"' + name + '\"]:checked')) return;
    } else if ((el.value || '').trim()) return;
    const ctrl = el.closest('[class*=\"select__control\"], [class*=\"_container\"]');
    if (ctrl && ctrl.querySelector('[class*=\"single-value\"], [class*=\"multi-value\"]')) return;
    let label = '';
    if (el.id) { const l = document.querySelector('label[for=\"' + el.id + '\"]'); if (l) label = txt(l.innerText); }
    if (!label) { const p = el.closest('label'); if (p) label = txt(p.innerText); }
    if (!label) label = el.name || el.id || t;
    out.push(label.slice(0, 60));
  });
  return out;
}
"""

CONFIRM_WORDS = (
    "thank you for applying",
    "application has been submitted",
    "received your application",
    "submitted successfully",
    "thanks for applying",
    "your application was submitted",
    "application submitted",
    "we received your application",
)
CONFIRM_URL_HINTS = ("confirmation", "thank", "submitted", "thanks", "applied", "success")

# Containers/headings ATSes render on a real confirmation screen (a structural signal, not just a
# word appearing somewhere in a long page).
_CONFIRM_SELECTORS = (
    '[class*="confirmation"]',
    '[id*="confirmation"]',
    '[data-ui*="confirmation"]',
    '[class*="application-confirmation"]',
    '[class*="post-application"]',
    '[class*="application-complete"]',
)
# Visible validation/error text that means the submit did NOT go through.
_ERROR_PHRASES = (
    "please complete",
    "please fill",
    "please correct",
    "please fix",
    "is required",
    "field is required",
    "required field",
    "cannot be blank",
    "there was a problem",
    "something went wrong",
)

_CAPTCHA_SELECTORS = (
    'iframe[src*="recaptcha"]',
    ".g-recaptcha",
    'iframe[src*="hcaptcha"]',
    ".h-captcha",
    "[data-sitekey]",
    'iframe[title*="challenge"]',
    'iframe[src*="turnstile"]',
)


def detect_captcha(page) -> bool:
    for sel in _CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


# Anti-bot interstitials that some ATSes (notably SmartRecruiters' oneclick-ui, via DataDome) serve
# to automation. We treat these like a CAPTCHA: never pretend to submit through them.
_BLOCK_PHRASES = (
    "access is temporarily restricted",
    "unusual activity from your device",
    "automated (bot) activity",
    "verify you are human",
    "are you a robot",
    "request blocked",
    "enable javascript and cookies to continue",
)
# Hosts of known anti-bot/CAPTCHA providers — often loaded in a child iframe, so the main body is
# empty while the challenge lives in a frame.
_BLOCK_FRAME_HOSTS = (
    "captcha-delivery.com",  # DataDome
    "datadome",
    "perimeterx",
    "px-cdn",
    "px-cloud",
    "challenges.cloudflare.com",
    "hcaptcha.com",
    "recaptcha",
)


def detect_blocked(page) -> bool:
    """True if the page is an anti-bot block/challenge (main page OR a child frame)."""
    try:
        body = (page.inner_text("body") or "").lower()
        if any(p in body for p in _BLOCK_PHRASES):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        for fr in page.frames:
            if any(h in (fr.url or "").lower() for h in _BLOCK_FRAME_HOSTS):
                return True
            try:
                txt = (fr.evaluate("() => (document.body ? document.body.innerText : '')") or "").lower()
            except Exception:  # noqa: BLE001
                continue
            if any(p in txt for p in _BLOCK_PHRASES):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def fill_first(page, selectors: list[str], value: str) -> bool:
    """Fill the first visible matching input with `value`. Returns True if filled."""
    if not value:
        return False
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.fill(value, timeout=4000)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def fill_by_label(page, needles: list[str], value: str, kinds: tuple[str, ...] = ("input", "textarea")) -> bool:
    """Fill the first visible, empty input/textarea whose computed label matches any needle.

    Resilient for React forms (Ashby/Workable) where stable CSS selectors don't exist but labels do.
    """
    if not value:
        return False
    low = [n.lower() for n in needles]
    for kind in kinds:
        els = page.locator(kind)
        for i in range(min(els.count(), 40)):
            el = els.nth(i)
            try:
                if not el.is_visible():
                    continue
                t = (el.get_attribute("type") or "").lower()
                if t in ("hidden", "file", "submit", "button", "checkbox", "radio"):
                    continue
                if (el.input_value() or "").strip():
                    continue
                label = (el.evaluate(LABEL_JS) or "").lower()
                if any(n in label for n in low):
                    el.fill(value, timeout=4000)
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def upload_first(page, selectors: list[str], path: str) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.set_input_files(path)
                page.wait_for_timeout(1500)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def missing_required(page) -> list[str]:
    junk = {"text", "i", "select", "textarea", "checkbox", "radio", ""}
    try:
        labels = page.evaluate(MISSING_JS)
    except Exception:  # noqa: BLE001
        return []
    clean = [lab for lab in labels if lab.strip().lower() not in junk and len(lab.strip()) > 2]
    return list(dict.fromkeys(clean))


def _decide_confirmation(
    url: str, body_text: str, has_confirm_element: bool, form_present: bool, has_error: bool
) -> str | None:
    """Pure verification decision (no Playwright) so it can be unit-tested exhaustively.

    A confirmation must be *corroborated*, not guessed from a URL alone:
      - explicit confirm phrase OR a confirmation container/heading  -> trust it (unless a form is
        still showing a validation error, i.e. the submit clearly failed); else
      - a confirm-y URL counts ONLY when the application form is gone and no error is visible.
    """
    body = (body_text or "").lower()
    text_confirm = any(w in body for w in CONFIRM_WORDS)
    url_confirm = any(w in (url or "").lower() for w in CONFIRM_URL_HINTS)

    if text_confirm or has_confirm_element:
        if has_error and form_present:
            return None
        return url
    if url_confirm and not form_present and not has_error:
        return url
    return None


def _form_present(page) -> bool:
    """True if an application form is still on the page — a strong 'not submitted yet' signal."""
    try:
        return (
            page.locator(
                'form input[type="email"], form input[type="text"], input[name="email"], '
                'button:has-text("Submit Application"), button[type="submit"]'
            ).count()
            > 0
        )
    except Exception:  # noqa: BLE001
        return False


def _confirm_element_present(page) -> bool:
    for sel in _CONFIRM_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def page_confirms(page) -> str | None:
    """Return the URL if the page is a *corroborated* submission confirmation, else None.

    Hardened: a confirm-y URL is no longer trusted on its own. We require a real confirmation phrase
    or container, or — for URL-only hints — that the application form is gone with no visible errors.
    """
    try:
        body = page.inner_text("body").lower()
    except Exception:  # noqa: BLE001
        body = ""
    has_error = any(p in body for p in _ERROR_PHRASES)
    return _decide_confirmation(
        url=page.url,
        body_text=body,
        has_confirm_element=_confirm_element_present(page),
        form_present=_form_present(page),
        has_error=has_error,
    )


def click_first(page, selectors: list[str], timeout: int = 8000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=timeout)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False
