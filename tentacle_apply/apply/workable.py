"""WorkableApplier — deterministic Tier-1 automation for Workable boards (apply.workable.com).

Workable's hosted apply form is clean and stable: `#firstname`, `#lastname`, `#email`, a `tel`
phone input, a single resume file input, and custom screening questions (often YES/NO radios).
Many Workable boards have no CAPTCHA, so unattended submit is realistic.

Flow mirrors the other Tier-1 templates: open the /apply page → confirm the form → fill standard
fields → upload the tailored resume → answer selects/free-text → screenshot. Dry run by default;
submit only after verifying a real confirmation. CAPTCHA/anti-bot blocks are detected, never faked.
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
    """Workable postings live at .../{token}/j/{shortcode}/; the form is at .../apply/."""
    u = (url or "").split("?")[0].rstrip("/")
    if u.endswith("/apply"):
        return u
    return u + "/apply"


class WorkableApplier:
    ats = "workable"

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
                log.info("workable apply start url=%s submit=%s", target, submit)
                page.goto(target, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                self._dismiss_cookies(page)

                if C.detect_blocked(page):
                    notes.append("anti-bot block on Workable form (run with --hitl to solve it yourself)")
                    return ApplyResult(status=ApplicationStatus.SKIPPED_CAPTCHA, notes=notes, screenshot=self._shot(page, "blocked"))

                if not self._form_present(page):
                    notes.append("no Workable application form found at this URL")
                    return ApplyResult(status=ApplicationStatus.FAILED, error="no application form found at URL", notes=notes, screenshot=self._shot(page, "noform"))

                self._fill_standard(page, applicant, filled, notes)
                self._upload_resume(page, applicant, filled, notes)
                self._answer_selects(page, applicant, filled, notes)
                self._answer_textareas(page, applicant, job_text, filled, notes, fill_freetext)

                captcha = C.detect_captcha(page)
                missing = C.missing_required(page)
                log.info("workable filled=%s captcha=%s missing=%d", filled, captcha, len(missing))
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
                log.exception("workable apply failed url=%s: %s", target, str(exc)[:200])
                return ApplyResult(status=ApplicationStatus.FAILED, error=str(exc)[:300], filled=filled, notes=notes, screenshot=self._shot(page, "error"))
            finally:
                ctx.close()
                browser.close()

    # --- steps -------------------------------------------------------------

    def _shot(self, page, tag: str) -> str:
        shot = screenshot_path(None, f"workable_{tag}")
        try:
            page.screenshot(path=str(shot), full_page=True)
            return str(shot)
        except Exception:  # noqa: BLE001
            return ""

    def _dismiss_cookies(self, page) -> None:
        C.click_first(page, ['button:has-text("Accept all")', 'button:has-text("Accept All")', "#onetrust-accept-btn-handler"], timeout=3000)

    def _form_present(self, page) -> bool:
        try:
            return page.locator('#firstname, input[name="firstname"], #email, input[type="email"]').count() > 0
        except Exception:  # noqa: BLE001
            return False

    def _fill_standard(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        mapping = {
            "first name": (["#firstname", 'input[name="firstname"]'], applicant.first_name),
            "last name": (["#lastname", 'input[name="lastname"]'], applicant.last_name),
            "email": (["#email", 'input[name="email"]', 'input[type="email"]'], applicant.email),
            "phone": (['input[type="tel"]', "#phone", 'input[name="phone"]'], applicant.phone),
        }
        for label, (sels, value) in mapping.items():
            if value and C.fill_first(page, sels, value):
                filled.append(label)
            elif value:
                notes.append(f"could not fill {label}")
        for kind, val in (("linkedin", applicant.links.get("linkedin", "")), ("github", applicant.links.get("github", ""))):
            if val and C.fill_by_label(page, [kind], val):
                filled.append(kind)

    def _upload_resume(self, page, applicant: Applicant, filled: list[str], notes: list[str]) -> None:
        if not applicant.resume_pdf or not Path(applicant.resume_pdf).exists():
            notes.append("no resume PDF to upload")
            return
        if C.upload_first(page, ['input[type="file"]'], str(applicant.resume_pdf)):
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
        clicked = C.click_first(page, ['button:has-text("Submit application")', 'button:has-text("Submit Application")', 'button:has-text("Submit")', 'button[type="submit"]'])
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
        print("  CAPTCHA detected on Workable — over to you.", flush=True)
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
