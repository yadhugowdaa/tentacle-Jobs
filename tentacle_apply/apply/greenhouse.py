"""GreenhouseApplier — deterministic Tier-1 automation for Greenhouse-hosted job boards.

Flow: open the posting → (optional) reveal the form → detect anti-bot → fill standard fields →
upload the tailored resume PDF → answer screening questions → check for unfilled required fields →
screenshot. By DEFAULT we stop here (dry run). Only with submit=True do we click submit and then
verify a real confirmation before reporting success. We never solve CAPTCHAs; we detect and skip.
"""

from __future__ import annotations

from pathlib import Path

from tentacle_apply.apply.answers import answer_choice, answer_text
from tentacle_apply.apply.base import Applicant, ApplyResult, screenshot_path
from tentacle_apply.db.models import ApplicationStatus
from tentacle_apply.log import get_logger

log = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

_LABEL_JS = """
(el) => {
  const txt = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  if (el.id) { const l = document.querySelector('label[for=\"' + el.id + '\"]'); if (l) return txt(l.innerText); }
  let p = el.closest('label'); if (p) return txt(p.innerText);
  if (el.getAttribute('aria-label')) return txt(el.getAttribute('aria-label'));
  const lb = el.getAttribute('aria-labelledby'); if (lb) { const e = document.getElementById(lb); if (e) return txt(e.innerText); }
  let c = el.closest('div');
  for (let k = 0; k < 6 && c; k++) { const l = c.querySelector('label, .select__label, legend'); if (l) return txt(l.innerText); c = c.parentElement; }
  return txt(el.name || '');
}
"""

_MISSING_JS = """
() => {
  const out = [];
  const txt = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('input, textarea, select').forEach((el) => {
    const req = el.required || el.getAttribute('aria-required') === 'true';
    if (!req) return;
    const t = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'file'].includes(t)) return;
    if ((el.value || '').trim()) return;
    // React-select keeps its value in component state, not the input — treat a combobox that
    // already shows a chosen value as filled.
    const ctrl = el.closest('[class*=\"select__control\"]');
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

_CONFIRM_WORDS = (
    "thank you for applying",
    "application has been submitted",
    "received your application",
    "submitted successfully",
    "thanks for applying",
    "your application was submitted",
)
_PLACEHOLDER_OPTS = ("select", "select...", "please select", "choose", "--", "")


class GreenhouseApplier:
    ats = "greenhouse"

    def __init__(self, headful: bool = False, timeout_ms: int = 30000, hitl_timeout_s: int = 300) -> None:
        self.headful = headful
        self.timeout_ms = timeout_ms
        self.hitl_timeout_s = hitl_timeout_s

    def apply(
        self,
        url: str,
        applicant: Applicant,
        job_text: str = "",
        submit: bool = False,
        interactive: bool = False,
        fill_freetext: bool | None = None,
    ) -> ApplyResult:
        from playwright.sync_api import sync_playwright

        # Generating grounded LLM answers for open-ended questions is the slow part. Default: only
        # do it when actually submitting (a dry run just records which free-text questions exist).
        if fill_freetext is None:
            fill_freetext = submit
        # Interactive (human-on-CAPTCHA) mode needs a visible browser so the user can act.
        headful = self.headful or interactive

        filled: list[str] = []
        notes: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headful)
            ctx = browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 1800})
            page = ctx.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                log.info("apply start url=%s submit=%s interactive=%s", url, submit, interactive)
                page.goto(url, wait_until="domcontentloaded")
                self._ensure_form(page)
                page.wait_for_timeout(800)

                self._fill_standard(page, applicant, filled, notes)
                self._upload_resume(page, applicant, filled, notes)
                self._answer_questions(page, applicant, job_text, filled, notes, fill_freetext)

                captcha = self._detect_captcha(page)  # checked after fill so the iframe has loaded
                missing = self._missing_required(page)
                log.info("filled=%s captcha=%s missing_required=%d", filled, captcha, len(missing))
                shot = screenshot_path(None, "submitted" if submit else "preview")
                try:
                    page.screenshot(path=str(shot), full_page=True)
                except Exception:  # noqa: BLE001
                    shot = ""
                if captcha:
                    notes.append("anti-bot/CAPTCHA detected on form")

                if not submit:
                    notes.append("DRY RUN: form filled but NOT submitted (pass --submit to send)")
                    return ApplyResult(
                        status=ApplicationStatus.QUEUED,
                        filled=filled,
                        missing_required=missing,
                        notes=notes,
                        screenshot=str(shot),
                    )

                # CAPTCHA present + not interactive → skip (never fake it).
                if captcha and not interactive:
                    return ApplyResult(
                        status=ApplicationStatus.SKIPPED_CAPTCHA,
                        filled=filled,
                        notes=notes + ["skipped: CAPTCHA present (run with --hitl to solve it yourself)"],
                        screenshot=str(shot),
                    )

                # CAPTCHA present + interactive → hand off to the human, then verify.
                if captcha and interactive:
                    conf_url = self._hitl_wait(page, missing, notes)
                    done_shot = screenshot_path(None, "confirmation")
                    try:
                        page.screenshot(path=str(done_shot), full_page=True)
                    except Exception:  # noqa: BLE001
                        done_shot = shot
                    if conf_url:
                        return ApplyResult(
                            status=ApplicationStatus.VERIFIED, confirmation_url=conf_url,
                            filled=filled, notes=notes, screenshot=str(done_shot), submitted=True,
                        )
                    return ApplyResult(
                        status=ApplicationStatus.SKIPPED_CAPTCHA,
                        error="no confirmation within wait window",
                        filled=filled, missing_required=missing, notes=notes, screenshot=str(done_shot),
                    )

                # No CAPTCHA → we can submit automatically, but only if nothing required is missing.
                if missing:
                    return ApplyResult(
                        status=ApplicationStatus.FAILED,
                        error="required fields unfilled",
                        filled=filled,
                        missing_required=missing,
                        notes=notes,
                        screenshot=str(shot),
                    )

                conf_url = self._submit_and_verify(page, notes)
                done_shot = screenshot_path(None, "confirmation")
                try:
                    page.screenshot(path=str(done_shot), full_page=True)
                except Exception:  # noqa: BLE001
                    done_shot = shot
                if conf_url:
                    return ApplyResult(
                        status=ApplicationStatus.VERIFIED,
                        confirmation_url=conf_url,
                        filled=filled,
                        notes=notes,
                        screenshot=str(done_shot),
                        submitted=True,
                    )
                return ApplyResult(
                    status=ApplicationStatus.FAILED,
                    error="submitted but no confirmation detected",
                    filled=filled,
                    notes=notes,
                    screenshot=str(done_shot),
                    submitted=True,
                )
            except Exception as exc:  # noqa: BLE001 - any failure is a logged, non-fatal outcome
                log.exception("apply failed for url=%s: %s", url, str(exc)[:200])
                shot = screenshot_path(None, "error")
                try:
                    page.screenshot(path=str(shot), full_page=True)
                except Exception:  # noqa: BLE001
                    shot = ""
                return ApplyResult(
                    status=ApplicationStatus.FAILED,
                    error=str(exc)[:300],
                    filled=filled,
                    notes=notes,
                    screenshot=str(shot),
                )
            finally:
                ctx.close()
                browser.close()

    # --- steps -------------------------------------------------------------

    def _ensure_form(self, page) -> None:
        if page.locator("#first_name").count() > 0:
            return
        for sel in (
            'button:has-text("Apply for this job")',
            'a:has-text("Apply for this job")',
            'button:has-text("Apply")',
            'a:has-text("Apply")',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=4000)
                    page.wait_for_timeout(1500)
                    return
            except Exception:  # noqa: BLE001
                continue

    def _detect_captcha(self, page) -> bool:
        for sel in (
            'iframe[src*="recaptcha"]',
            ".g-recaptcha",
            'iframe[src*="hcaptcha"]',
            "[data-sitekey]",
            'iframe[title*="challenge"]',
        ):
            try:
                if page.locator(sel).count() > 0:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _fill_one(self, page, selectors: list[str], value: str) -> bool:
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

    def _fill_standard(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        mapping = {
            "first name": (["#first_name", 'input[name="first_name"]', 'input[autocomplete="given-name"]'], applicant.first_name),
            "last name": (["#last_name", 'input[name="last_name"]', 'input[autocomplete="family-name"]'], applicant.last_name),
            "email": (["#email", 'input[type="email"]', 'input[name="email"]'], applicant.email),
            "phone": (["#phone", 'input[type="tel"]', 'input[name="phone"]'], applicant.phone),
        }
        for label, (sels, value) in mapping.items():
            if self._fill_one(page, sels, value):
                filled.append(label)
            elif value:
                notes.append(f"could not fill {label}")

    def _upload_resume(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        if not applicant.resume_pdf or not Path(applicant.resume_pdf).exists():
            notes.append("no resume PDF to upload")
            return
        for sel in ('input#resume[type="file"]', '#resume input[type="file"]', 'input[type="file"]'):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.set_input_files(str(applicant.resume_pdf))
                    page.wait_for_timeout(1500)
                    filled.append("resume")
                    return
            except Exception:  # noqa: BLE001
                continue
        notes.append("resume file input not found")

    def _answer_questions(
        self, page, applicant: Applicant, job_text: str, filled: list[str], notes: list[str], fill_freetext: bool
    ) -> None:
        self._answer_selects(page, applicant, filled, notes)
        self._answer_comboboxes(page, applicant, filled, notes)
        self._answer_textareas(page, applicant, job_text, filled, notes, fill_freetext)

    def _answer_selects(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        # Don't gate on visibility: Greenhouse often hides the native <select> behind a styled
        # widget, but select_option still works on the underlying element.
        selects = page.locator("select")
        for i in range(min(selects.count(), 25)):
            el = selects.nth(i)
            try:
                label = el.evaluate(_LABEL_JS) or ""
                raw_opts = el.locator("option").all_text_contents()
                opts = [o.strip() for o in raw_opts if o.strip().lower() not in _PLACEHOLDER_OPTS]
                choice = answer_choice(label, opts, applicant)
                if choice:
                    el.select_option(label=choice)
                    filled.append(f"select:{label[:28]}")
            except Exception:  # noqa: BLE001
                continue

    def _answer_comboboxes(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        """Modern Greenhouse boards render dropdowns as React-select comboboxes (div-based)."""
        controls = page.locator('[class*="select__control"]')
        for i in range(min(controls.count(), 25)):
            ctrl = controls.nth(i)
            try:
                if not ctrl.is_visible():
                    continue
                if ctrl.locator('[class*="single-value"], [class*="multi-value"]').count() > 0:
                    continue  # already has a value
                label = ctrl.evaluate(_LABEL_JS) or ""
                ctrl.click()
                page.wait_for_timeout(300)
                options = page.locator('[class*="select__option"]')
                n = min(options.count(), 300)
                opt_texts = [options.nth(j).inner_text().strip() for j in range(n)]
                choice = answer_choice(label, [o for o in opt_texts if o], applicant)
                if choice and choice in opt_texts:
                    options.nth(opt_texts.index(choice)).click(timeout=3000)
                    filled.append(f"combo:{label[:24]}")
                else:
                    page.keyboard.press("Escape")
                    if label:
                        notes.append(f"dropdown left for review: {label[:48]}")
            except Exception:  # noqa: BLE001
                try:
                    page.keyboard.press("Escape")
                except Exception:  # noqa: BLE001
                    pass
                continue

    def _answer_textareas(
        self, page, applicant: Applicant, job_text: str, filled: list[str], notes: list[str], fill_freetext: bool
    ) -> None:
        areas = page.locator("textarea")
        for i in range(min(areas.count(), 12)):
            el = areas.nth(i)
            try:
                if not el.is_visible() or (el.input_value() or "").strip():
                    continue
                label = el.evaluate(_LABEL_JS) or ""
                if "cover" in label.lower() and applicant.cover_letter:
                    el.fill(applicant.cover_letter)
                    filled.append("cover letter")
                    continue
                if not fill_freetext:
                    notes.append(f"free-text question (answered on submit): {label[:48]}")
                    continue
                ans = answer_text(label, applicant, job_text)
                if ans:
                    el.fill(ans)
                    filled.append(f"text:{label[:28]}")
            except Exception:  # noqa: BLE001
                continue

    def _missing_required(self, page) -> list[str]:
        junk = {"text", "i", "select", "textarea", "checkbox", "radio", ""}
        try:
            labels = page.evaluate(_MISSING_JS)
        except Exception:  # noqa: BLE001
            return []
        clean = [lab for lab in labels if lab.strip().lower() not in junk and len(lab.strip()) > 2]
        return list(dict.fromkeys(clean))

    def _hitl_wait(self, page, missing: list[str], notes: list[str]) -> str | None:
        """Human-in-the-loop: the form is fully filled; the user solves the CAPTCHA and submits.

        We poll for a real confirmation so success is still verified (not assumed). Everything up to
        the CAPTCHA was automated — the human only handles the challenge + final submit click.
        """
        import sys
        import time

        print("\n" + "=" * 64, flush=True)
        print("  CAPTCHA detected — over to you for a moment.", flush=True)
        print("  The application is fully filled in the open browser window.", flush=True)
        if missing:
            print(f"  Please complete these flagged fields: {', '.join(missing[:6])}", flush=True)
        print("  Solve the CAPTCHA, then click Submit. I'll detect the confirmation.", flush=True)
        print(f"  Waiting up to {self.hitl_timeout_s}s…", flush=True)
        print("=" * 64, flush=True)

        deadline = time.time() + self.hitl_timeout_s
        last_beat = 0.0
        while time.time() < deadline:
            try:
                body = page.inner_text("body").lower()
            except Exception:  # noqa: BLE001
                body = ""
            if any(w in body for w in _CONFIRM_WORDS) or any(
                w in page.url.lower() for w in ("confirmation", "thank", "submitted")
            ):
                print("  Confirmation detected — submission verified.", flush=True)
                return page.url
            remaining = int(deadline - time.time())
            if remaining // 30 != last_beat:
                last_beat = remaining // 30
                print(f"  …still waiting ({remaining}s left)", flush=True)
                sys.stdout.flush()
            page.wait_for_timeout(2000)
        notes.append("HITL timed out waiting for confirmation")
        return None

    def _submit_and_verify(self, page, notes: list[str]) -> str | None:
        clicked = False
        for sel in (
            'button:has-text("Submit application")',
            'button:has-text("Submit Application")',
            'button:has-text("Submit")',
            'button[type="submit"]',
            'input[type="submit"]',
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=8000)
                    clicked = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not clicked:
            notes.append("submit button not found")
            return None
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(3000)
        try:
            body = page.inner_text("body").lower()
        except Exception:  # noqa: BLE001
            body = ""
        if any(w in body for w in _CONFIRM_WORDS):
            return page.url
        if any(w in page.url.lower() for w in ("confirmation", "thank", "submitted")):
            return page.url
        return None
