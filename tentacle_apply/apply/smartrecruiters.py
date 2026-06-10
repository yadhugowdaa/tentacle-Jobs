"""SmartRecruitersApplier — Tier-1 automation for SmartRecruiters boards (jobs.smartrecruiters.com).

Honesty note: SmartRecruiters' application flow ("oneclick-ui") is **bot-protected** — it commonly
serves an "Access is temporarily restricted / unusual activity" interstitial to headless automation.
We detect that and report `skipped_captcha` (use --hitl with a real browser) rather than faking a
submit. Discovery via the public Posting API is unaffected; only the apply form is guarded.

When the form *is* reachable (e.g. HITL/headful), it's a React form: we fill name/email/phone by
label, upload the resume, answer free-text, and submit only after verifying a real confirmation.
"""

from __future__ import annotations

from pathlib import Path

from tentacle_apply.apply import _common as C
from tentacle_apply.apply.answers import answer_text
from tentacle_apply.apply.base import Applicant, ApplyResult, screenshot_path
from tentacle_apply.db.models import ApplicationStatus
from tentacle_apply.log import get_logger

log = get_logger(__name__)


class SmartRecruitersApplier:
    ats = "smartrecruiters"

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

        if fill_freetext is None:
            fill_freetext = submit
        headful = self.headful or interactive

        filled: list[str] = []
        notes: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headful)
            ctx = browser.new_context(user_agent=C._UA, viewport={"width": 1280, "height": 1800})
            page = ctx.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                log.info("smartrecruiters apply start url=%s submit=%s", url, submit)
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                self._dismiss_cookies(page)
                self._reveal_form(page)
                self._settle(page)

                # Anti-bot wall is the common headless outcome — report it honestly. The block page
                # often renders late, so we check after letting the apply UI settle (and again below).
                if C.detect_blocked(page):
                    notes.append(
                        "SmartRecruiters served an anti-bot block (its apply form guards against "
                        "automation). Discovery works; apply needs --hitl in a real browser, or manual."
                    )
                    return ApplyResult(status=ApplicationStatus.SKIPPED_CAPTCHA, notes=notes, screenshot=self._shot(page, "blocked"))

                if not self._form_present(page):
                    # Give a late-rendering block page one more chance before calling it a hard failure.
                    page.wait_for_timeout(2500)
                    if C.detect_blocked(page):
                        notes.append(
                            "SmartRecruiters served an anti-bot block (its apply form guards against "
                            "automation). Discovery works; apply needs --hitl in a real browser, or manual."
                        )
                        return ApplyResult(status=ApplicationStatus.SKIPPED_CAPTCHA, notes=notes, screenshot=self._shot(page, "blocked"))
                    notes.append("no SmartRecruiters application form found (likely anti-bot or multi-step flow)")
                    return ApplyResult(status=ApplicationStatus.FAILED, error="no application form found at URL", notes=notes, screenshot=self._shot(page, "noform"))

                self._fill_standard(page, applicant, filled, notes)
                self._upload_resume(page, applicant, filled, notes)
                self._answer_textareas(page, applicant, job_text, filled, notes, fill_freetext)

                captcha = C.detect_captcha(page) or C.detect_blocked(page)
                missing = C.missing_required(page)
                log.info("smartrecruiters filled=%s captcha=%s missing=%d", filled, captcha, len(missing))
                shot = self._shot(page, "submitted" if submit else "preview")
                if captcha:
                    notes.append("anti-bot/CAPTCHA detected on form")

                if not submit:
                    notes.append("DRY RUN: form filled but NOT submitted (pass --submit to send)")
                    return ApplyResult(status=ApplicationStatus.QUEUED, filled=filled, missing_required=missing, notes=notes, screenshot=shot)

                if captcha and not interactive:
                    return ApplyResult(status=ApplicationStatus.SKIPPED_CAPTCHA, filled=filled, notes=notes + ["skipped: anti-bot/CAPTCHA present (run with --hitl)"], screenshot=shot)

                if captcha and interactive:
                    conf = self._hitl_wait(page, missing, notes)
                    done = self._shot(page, "confirmation") or shot
                    if conf:
                        return ApplyResult(status=ApplicationStatus.VERIFIED, confirmation_url=conf, filled=filled, notes=notes, screenshot=done, submitted=True)
                    return ApplyResult(status=ApplicationStatus.SKIPPED_CAPTCHA, error="no confirmation within wait window", filled=filled, missing_required=missing, notes=notes, screenshot=done)

                if missing:
                    return ApplyResult(status=ApplicationStatus.FAILED, error="required fields unfilled", filled=filled, missing_required=missing, notes=notes, screenshot=shot)

                conf = self._submit_and_verify(page, notes)
                done = self._shot(page, "confirmation") or shot
                if conf:
                    return ApplyResult(status=ApplicationStatus.VERIFIED, confirmation_url=conf, filled=filled, notes=notes, screenshot=done, submitted=True)
                return ApplyResult(status=ApplicationStatus.FAILED, error="submitted but no confirmation detected", filled=filled, notes=notes, screenshot=done, submitted=True)
            except Exception as exc:  # noqa: BLE001
                log.exception("smartrecruiters apply failed url=%s: %s", url, str(exc)[:200])
                return ApplyResult(status=ApplicationStatus.FAILED, error=str(exc)[:300], filled=filled, notes=notes, screenshot=self._shot(page, "error"))
            finally:
                ctx.close()
                browser.close()

    # --- steps -------------------------------------------------------------

    def _shot(self, page, tag: str) -> str:
        shot = screenshot_path(None, f"sr_{tag}")
        try:
            page.screenshot(path=str(shot), full_page=True)
            return str(shot)
        except Exception:  # noqa: BLE001
            return ""

    def _settle(self, page) -> None:
        """The apply flow redirects to a JS-heavy oneclick-ui; let it finish rendering."""
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(2000)

    def _dismiss_cookies(self, page) -> None:
        C.click_first(page, ["#onetrust-accept-btn-handler", 'button:has-text("Accept All")', 'button:has-text("Accept all")'], timeout=3000)

    def _reveal_form(self, page) -> None:
        if self._form_present(page):
            return
        C.click_first(page, ["button:has-text(\"I'm interested\")", 'a:has-text("Apply")', 'button:has-text("Apply")'], timeout=4000)
        page.wait_for_timeout(2000)

    def _form_present(self, page) -> bool:
        try:
            return page.locator('input[type="email"], input[name="email"], #email, input[name="firstName"]').count() > 0
        except Exception:  # noqa: BLE001
            return False

    def _fill_standard(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        if C.fill_first(page, ['input[name="firstName"]', "#firstName"], applicant.first_name) or C.fill_by_label(page, ["first name"], applicant.first_name):
            filled.append("first name")
        if C.fill_first(page, ['input[name="lastName"]', "#lastName"], applicant.last_name) or C.fill_by_label(page, ["last name"], applicant.last_name):
            filled.append("last name")
        if C.fill_first(page, ['input[name="email"]', "#email", 'input[type="email"]'], applicant.email) or C.fill_by_label(page, ["email"], applicant.email):
            filled.append("email")
        if applicant.phone and (C.fill_first(page, ['input[name="phoneNumber"]', 'input[type="tel"]'], applicant.phone) or C.fill_by_label(page, ["phone"], applicant.phone)):
            filled.append("phone")

    def _upload_resume(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        if not applicant.resume_pdf or not Path(applicant.resume_pdf).exists():
            notes.append("no resume PDF to upload")
            return
        if C.upload_first(page, ['input[type="file"]'], str(applicant.resume_pdf)):
            filled.append("resume")
        else:
            notes.append("resume file input not found")

    def _answer_textareas(self, page, applicant: Applicant, job_text: str, filled: list[str], notes: list[str], fill_freetext: bool) -> None:
        areas = page.locator("textarea")
        for i in range(min(areas.count(), 12)):
            el = areas.nth(i)
            try:
                if not el.is_visible() or (el.input_value() or "").strip():
                    continue
                label = el.evaluate(C.LABEL_JS) or ""
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
                    filled.append(f"text:{label[:24]}")
            except Exception:  # noqa: BLE001
                continue

    def _submit_and_verify(self, page, notes: list[str]) -> str | None:
        clicked = C.click_first(page, ['button:has-text("Submit application")', 'button:has-text("Apply")', 'button:has-text("Submit")', 'button[type="submit"]'])
        if not clicked:
            notes.append("submit button not found")
            return None
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(3000)
        return C.page_confirms(page)

    def _hitl_wait(self, page, missing: list[str], notes: list[str]) -> str | None:
        import time

        print("\n" + "=" * 64, flush=True)
        print("  Anti-bot/CAPTCHA on SmartRecruiters — over to you.", flush=True)
        print("  Complete the application in the open browser, then Submit.", flush=True)
        if missing:
            print(f"  Please complete: {', '.join(missing[:6])}", flush=True)
        print(f"  Waiting up to {self.hitl_timeout_s}s…", flush=True)
        print("=" * 64, flush=True)
        deadline = time.time() + self.hitl_timeout_s
        while time.time() < deadline:
            conf = C.page_confirms(page)
            if conf:
                print("  Confirmation detected — submission verified.", flush=True)
                return conf
            page.wait_for_timeout(2000)
        notes.append("HITL timed out waiting for confirmation")
        return None
