"""LeverApplier — deterministic Tier-1 automation for Lever-hosted job boards (jobs.lever.co).

Lever forms are stable and refreshingly simple: a single full-name field, email/phone/company,
a resume file input, optional link fields, an "additional information" textarea, and EEO/custom
question cards. Many Lever boards have NO CAPTCHA, so unattended submit is genuinely possible.

Flow mirrors the Greenhouse template: open the /apply page → confirm the form exists → fill
standard fields → upload the tailored resume → answer selects/EEO + free-text → screenshot. Dry run
by default; submit only with submit=True after verifying a real confirmation. CAPTCHA is never faked.
"""

from __future__ import annotations

from pathlib import Path

from tentacle_apply.apply import _common as C
from tentacle_apply.apply.answers import answer_choice, answer_text
from tentacle_apply.apply.base import Applicant, ApplyResult, screenshot_path
from tentacle_apply.db.models import ApplicationStatus
from tentacle_apply.log import get_logger

log = get_logger(__name__)

_PLACEHOLDER_OPTS = ("select", "select...", "please select", "choose", "--", "")


def _apply_url(url: str) -> str:
    """Lever postings live at .../{org}/{id}; the form is at .../{org}/{id}/apply."""
    u = (url or "").split("?")[0].rstrip("/")
    if u.endswith("/apply"):
        return u
    return u + "/apply"


class LeverApplier:
    ats = "lever"

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
        target = _apply_url(url)

        filled: list[str] = []
        notes: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headful)
            ctx = browser.new_context(user_agent=C._UA, viewport={"width": 1280, "height": 1800})
            page = ctx.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                log.info("lever apply start url=%s submit=%s", target, submit)
                page.goto(target, wait_until="domcontentloaded")
                page.wait_for_timeout(800)

                if not self._form_present(page):
                    notes.append("no Lever application form found at this URL")
                    shot = self._shot(page, "noform")
                    return ApplyResult(status=ApplicationStatus.FAILED, error="no application form found at URL", notes=notes, screenshot=shot)

                self._fill_standard(page, applicant, filled, notes)
                self._upload_resume(page, applicant, filled, notes)
                self._answer_selects(page, applicant, filled, notes)
                self._answer_textareas(page, applicant, job_text, filled, notes, fill_freetext)

                captcha = C.detect_captcha(page)
                missing = C.missing_required(page)
                log.info("lever filled=%s captcha=%s missing=%d", filled, captcha, len(missing))
                shot = self._shot(page, "submitted" if submit else "preview")
                if captcha:
                    notes.append("anti-bot/CAPTCHA detected on form")

                if not submit:
                    notes.append("DRY RUN: form filled but NOT submitted (pass --submit to send)")
                    return ApplyResult(status=ApplicationStatus.QUEUED, filled=filled, missing_required=missing, notes=notes, screenshot=shot)

                if captcha and not interactive:
                    return ApplyResult(status=ApplicationStatus.SKIPPED_CAPTCHA, filled=filled, notes=notes + ["skipped: CAPTCHA present (run with --hitl)"], screenshot=shot)

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
                log.exception("lever apply failed url=%s: %s", target, str(exc)[:200])
                return ApplyResult(status=ApplicationStatus.FAILED, error=str(exc)[:300], filled=filled, notes=notes, screenshot=self._shot(page, "error"))
            finally:
                ctx.close()
                browser.close()

    # --- steps -------------------------------------------------------------

    def _shot(self, page, tag: str) -> str:
        shot = screenshot_path(None, f"lever_{tag}")
        try:
            page.screenshot(path=str(shot), full_page=True)
            return str(shot)
        except Exception:  # noqa: BLE001
            return ""

    def _form_present(self, page) -> bool:
        try:
            return page.locator('input[name="name"], input[name="email"], input[type="email"]').count() > 0
        except Exception:  # noqa: BLE001
            return False

    def _fill_standard(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        full = f"{applicant.first_name} {applicant.last_name}".strip()
        mapping = {
            "name": (['input[name="name"]', "#name"], full),
            "email": (['input[name="email"]', 'input[type="email"]'], applicant.email),
            "phone": (['input[name="phone"]', 'input[type="tel"]'], applicant.phone),
            "company": (['input[name="org"]'], ""),
            "linkedin": (['input[name="urls[LinkedIn]"]', 'input[name="urls[Linkedin]"]'], applicant.links.get("linkedin", "")),
            "github": (['input[name="urls[GitHub]"]', 'input[name="urls[Github]"]'], applicant.links.get("github", "")),
            "portfolio": (['input[name="urls[Portfolio]"]'], applicant.links.get("website", "")),
        }
        for label, (sels, value) in mapping.items():
            if value and C.fill_first(page, sels, value):
                filled.append(label)

    def _upload_resume(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        if not applicant.resume_pdf or not Path(applicant.resume_pdf).exists():
            notes.append("no resume PDF to upload")
            return
        if C.upload_first(page, ['input[name="resume"]', "#resume-upload-input", 'input[type="file"]'], str(applicant.resume_pdf)):
            filled.append("resume")
        else:
            notes.append("resume file input not found")

    def _answer_selects(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        selects = page.locator("select")
        for i in range(min(selects.count(), 25)):
            el = selects.nth(i)
            try:
                label = el.evaluate(C.LABEL_JS) or ""
                raw = el.locator("option").all_text_contents()
                opts = [o.strip() for o in raw if o.strip().lower() not in _PLACEHOLDER_OPTS]
                choice = answer_choice(label, opts, applicant)
                if choice:
                    el.select_option(label=choice)
                    filled.append(f"select:{label[:24]}")
            except Exception:  # noqa: BLE001
                continue

    def _answer_textareas(self, page, applicant: Applicant, job_text: str, filled: list[str], notes: list[str], fill_freetext: bool) -> None:
        areas = page.locator("textarea")
        for i in range(min(areas.count(), 12)):
            el = areas.nth(i)
            try:
                if not el.is_visible() or (el.input_value() or "").strip():
                    continue
                label = el.evaluate(C.LABEL_JS) or ""
                name = (el.get_attribute("name") or "").lower()
                # Lever's free "additional information" box → use the cover letter.
                if ("comment" in name or "cover" in label.lower() or "additional" in label.lower()) and applicant.cover_letter:
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
        clicked = C.click_first(page, ['button#btn-submit', 'button:has-text("Submit application")', 'button:has-text("Submit")', 'button[type="submit"]'])
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
        print("  CAPTCHA detected on Lever — over to you.", flush=True)
        print("  The application is filled in the open browser. Solve the CAPTCHA + Submit.", flush=True)
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
