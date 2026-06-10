"""WorkdayApplier — honesty-first handling for Workday career sites (*.myworkdayjobs.com).

Why this isn't a normal Tier-1 form-filler: Workday gates *every* submission behind creating a
per-employer account (email + password) before the multi-step application wizard even appears. We
deliberately do NOT automate account creation / credential storage, so faking an unattended submit
here would be a lie. Instead we:

  - PREPARE (default): open the posting, confirm the apply path exists, and report QUEUED with a clear
    note that Workday needs a human-driven apply (account creation). Tailoring/ranking still happen —
    discovery is the real value Workday adds.
  - HITL (--hitl): open a real browser at the posting so the user can sign in / create the account and
    finish the wizard themselves; we then watch for a confirmation and verify it.
  - SUBMIT unattended: refused honestly (reported as skipped, needs a human), never a fake submit.
"""

from __future__ import annotations

from tentacle_apply.apply import _common as C
from tentacle_apply.apply.base import Applicant, ApplyResult, screenshot_path
from tentacle_apply.db.models import ApplicationStatus
from tentacle_apply.log import get_logger

log = get_logger(__name__)

_GATE_NOTE = (
    "Workday requires creating a per-employer account (email + password) before its application "
    "wizard — we don't automate account creation. Discovery/tailoring done; finish via --hitl in a "
    "real browser, or apply manually."
)


class WorkdayApplier:
    ats = "workday"

    def __init__(self, headful: bool = False, timeout_ms: int = 30000, hitl_timeout_s: int = 420) -> None:
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

        headful = self.headful or interactive
        notes: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headful)
            ctx = browser.new_context(user_agent=C._UA, viewport={"width": 1280, "height": 1800})
            page = ctx.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                log.info("workday apply start url=%s submit=%s hitl=%s", url, submit, interactive)
                page.goto(url, wait_until="domcontentloaded")
                self._settle(page)
                self._dismiss_cookies(page)

                if C.detect_blocked(page):
                    notes.append("Workday served an anti-bot block; finish via --hitl or apply manually.")
                    return ApplyResult(status=ApplicationStatus.SKIPPED_CAPTCHA, notes=notes, screenshot=self._shot(page, "blocked"))

                has_apply = self._apply_path_present(page)
                shot = self._shot(page, "preview")

                # HITL: hand the open browser to the user to sign in + complete the wizard.
                if interactive:
                    conf = self._hitl_wait(page, notes)
                    done = self._shot(page, "confirmation") or shot
                    if conf:
                        return ApplyResult(status=ApplicationStatus.VERIFIED, confirmation_url=conf, notes=notes, screenshot=done, submitted=True)
                    return ApplyResult(status=ApplicationStatus.SKIPPED_CAPTCHA, error="no confirmation within wait window", notes=notes + [_GATE_NOTE], screenshot=done)

                # Unattended submit is refused honestly — never a fake submit through the account gate.
                if submit:
                    return ApplyResult(status=ApplicationStatus.SKIPPED_CAPTCHA, notes=notes + [_GATE_NOTE], screenshot=shot)

                # PREPARE: report readiness honestly.
                if not has_apply:
                    notes.append("no Apply control found on the Workday posting (it may be closed or already filled).")
                    return ApplyResult(status=ApplicationStatus.FAILED, error="no apply path found", notes=notes, screenshot=shot)
                notes.append("DRY RUN: Workday posting reached. " + _GATE_NOTE)
                return ApplyResult(status=ApplicationStatus.QUEUED, notes=notes, screenshot=shot)
            except Exception as exc:  # noqa: BLE001
                log.exception("workday apply failed url=%s: %s", url, str(exc)[:200])
                return ApplyResult(status=ApplicationStatus.FAILED, error=str(exc)[:300], notes=notes, screenshot=self._shot(page, "error"))
            finally:
                ctx.close()
                browser.close()

    # --- steps -------------------------------------------------------------

    def _shot(self, page, tag: str) -> str:
        shot = screenshot_path(None, f"wd_{tag}")
        try:
            page.screenshot(path=str(shot), full_page=True)
            return str(shot)
        except Exception:  # noqa: BLE001
            return ""

    def _settle(self, page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1500)

    def _dismiss_cookies(self, page) -> None:
        C.click_first(
            page,
            ["#onetrust-accept-btn-handler", 'button:has-text("Accept All")', 'button:has-text("Accept all")', 'button:has-text("Accept Cookies")'],
            timeout=3000,
        )

    def _apply_path_present(self, page) -> bool:
        try:
            return page.locator(
                'a[data-automation-id="adventureButton"], button[data-automation-id="adventureButton"], '
                'a:has-text("Apply"), button:has-text("Apply")'
            ).count() > 0
        except Exception:  # noqa: BLE001
            return False

    def _hitl_wait(self, page, notes: list[str]) -> str | None:
        import time

        print("\n" + "=" * 64, flush=True)
        print("  Workday needs a human: it requires creating an account to apply.", flush=True)
        print("  In the open browser: click Apply, create the account, and complete the wizard.", flush=True)
        print(f"  Waiting up to {self.hitl_timeout_s}s for a confirmation…", flush=True)
        print("=" * 64, flush=True)
        deadline = time.time() + self.hitl_timeout_s
        while time.time() < deadline:
            conf = C.page_confirms(page)
            if conf:
                print("  Confirmation detected — submission verified.", flush=True)
                return conf
            page.wait_for_timeout(2500)
        notes.append("HITL timed out waiting for confirmation")
        return None
