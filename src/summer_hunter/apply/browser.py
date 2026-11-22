"""Fill an application form in a real browser (Playwright, Chromium) and, only when asked, submit it.

`fill(..., submit=False)` types every answer except the files, takes a full-page screenshot and closes the
browser: nothing leaves the machine. `submit=True` attaches the files first, then fills, clicks the send button
and reads the page that follows. A captcha is never solved: the run stops and says so.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from summer_hunter.apply.answers import Answer

log = logging.getLogger(__name__)

SUCCESS = re.compile(r"thank(s| you) for (applying|your (application|interest))|application (has been |was )?"
                     r"(submitted|received|sent)|we('ve| have) received your application|successfully (submitted|applied)|"
                     r"your application is (in|complete)\b", re.I)
SUCCESS_URL = re.compile(r"confirmation|thank|success|submitted", re.I)
CODE_TEXT = re.compile(r"verification code (was|has been) sent|enter the \d+-character code|security code", re.I)
CODE_BOXES = "input[id^='security-input']:visible, fieldset:has-text('Security code') input:visible, input[autocomplete='one-time-code']:visible"
CAPTCHA_FRAMES = "iframe[src*='hcaptcha.com'][src*='challenge'], iframe[title*='challenge' i], iframe[src*='recaptcha/api2/bframe'], iframe[src*='recaptcha/enterprise/bframe']"


@dataclass
class Outcome:
    status: str                  # filled | submitted | captcha | invalid | unconfirmed | blocked | error
    screenshot: Path | None = None
    problems: list[str] = field(default_factory=list)
    detail: str = ""
    clicked: bool = False        # the send button was (maybe) pressed: the outcome can never be retried blindly
    failed: list[str] = field(default_factory=list)      # keys of fields the page did not take
    notes: list[str] = field(default_factory=list)
    blocked_requests: int = 0


def _visible_captcha(page) -> bool:  # noqa: ANN001
    for fr in page.locator(CAPTCHA_FRAMES).all():
        try:
            box = fr.bounding_box()
        except Exception:  # noqa: BLE001
            box = None
        if box and box["width"] > 50 and box["height"] > 50:
            return True
    return False


class Filler:
    def __init__(self, page, kind: str, timeout_ms: int) -> None:  # noqa: ANN001
        self.p, self.kind, self.t = page, kind, timeout_ms
        self.problems: list[str] = []
        self.notes: list[str] = []
        self.failed: set[str] = set()

    def fail(self, a: Answer, msg: str) -> None:
        self.problems.append(msg)
        self.failed.add(a.key)

    # locating ---------------------------------------------------------------------------------
    def el(self, a: Answer):  # noqa: ANN201
        if self.kind == "lever":
            return self.p.locator(f'[name="{a.key}"]').first
        return self.p.locator(f'[id="{a.key}"]').first

    def entry(self, a: Answer):  # noqa: ANN201
        """Ashby: the block that holds one question (label + its widgets)."""
        label = self.p.locator(f'label[for="{a.key}"]')
        if label.count():
            return label.first.locator("xpath=ancestor::div[contains(@class,'fieldEntry') or contains(@class,'field-entry')][1]")
        words = a.label[:60]
        return self.p.locator("div[class*='fieldEntry'], div[class*='field-entry']").filter(has_text=words).first

    # widgets ----------------------------------------------------------------------------------
    def combo(self, el, text: str, exact: str | None = None, search: str | None = None) -> bool:  # noqa: ANN001
        """React-style combobox: type, wait for the options, click the wanted one."""
        el.click()
        el.fill("")
        el.type(search or text, delay=25)
        opts = self.p.locator("[role='option']:visible")
        try:
            opts.first.wait_for(state="visible", timeout=8000)
        except Exception:  # noqa: BLE001
            self.p.keyboard.press("Escape")
            return False
        time.sleep(0.4)
        want = (exact or text).strip().lower()
        texts = [t.strip().lower() for t in opts.all_inner_texts()]
        idx = next((i for i, t in enumerate(texts) if t == want), None)
        if idx is None:
            idx = next((i for i, t in enumerate(texts) if t.startswith(want) or want in t), None)
        if idx is None:
            self.p.keyboard.press("Escape")
            return False
        opts.nth(idx).click()
        return True

    def set_one(self, a: Answer, attach: bool) -> None:
        v = a.value
        if v in (None, "", []):
            return
        if a.kind == "file":
            if attach:
                self._file(a)
            return
        try:
            getattr(self, f"_{self.kind}")(a, v)
        except Exception as exc:  # noqa: BLE001 - one field never stops the others
            self.fail(a, f"{a.label[:60]} : {type(exc).__name__}")
            log.info("field fill failed", extra={"field": a.key, "error": str(exc)[:200]})

    def _file(self, a: Answer) -> None:
        try:
            if self.kind == "lever" and a.role == "resume":
                self.p.locator('input[type="file"][name="resume"]').set_input_files(a.value)
                self.p.wait_for_timeout(4000)    # Lever parses the CV and may prefill fields: fill after it
            elif self.kind == "lever":
                self.el(a).set_input_files(a.value)
            else:
                self.p.locator(f'input[type="file"][id="{a.key}"]').set_input_files(a.value)
                self.p.wait_for_timeout(2500)
        except Exception as exc:  # noqa: BLE001
            self.fail(a, f"{a.label[:60]} (fichier) : {type(exc).__name__}")

    # Greenhouse (job-boards.greenhouse.io, React form) ----------------------------------------
    def _greenhouse(self, a: Answer, v: Any) -> None:
        el = self.el(a)
        if a.kind in ("text", "textarea", "email", "tel", "number"):
            if a.role == "phone":
                cc = self.p.locator('[id="country"]')
                if cc.count() and cc.first.is_visible():
                    self.combo(cc.first, "France", search="France")
            el.fill(str(v))
        elif a.kind == "location":
            if not self.combo(el, str(v), search=str(a.extra.get("search") or str(v).split(",")[0])):
                self.fail(a, f"{a.label[:60]} : ville introuvable dans la liste")
        elif a.kind == "select":
            search = a.extra.get("search")
            if not self.combo(el, str(v), exact=str(v), search=search):
                fb = a.extra.get("fallback")
                if fb and self.combo(el, fb, exact=fb):
                    self.notes.append(f"{a.label[:60]} : « {str(v)[:40]} » absent de la liste, « {fb} » choisi")
                else:
                    self.fail(a, f"{a.label[:60]} : option « {str(v)[:40]} » introuvable")
        elif a.kind == "multiselect":
            for item in v if isinstance(v, list) else [v]:
                if not self.combo(el, str(item), exact=str(item)):
                    self.fail(a, f"{a.label[:60]} : option « {str(item)[:40]} » introuvable")
        elif a.kind == "checkbox":
            el.check() if v else el.uncheck()

    # Lever (server form) ----------------------------------------------------------------------
    def _lever(self, a: Answer, v: Any) -> None:
        if a.kind in ("text", "textarea", "email", "tel", "number"):
            self.el(a).fill(str(v))
        elif a.kind == "location":
            el = self.el(a)
            el.fill("")
            el.type(str(a.extra.get("search") or v), delay=30)
            res = self.p.locator(".dropdown-results > div, .location-results > div")
            try:
                res.first.wait_for(state="visible", timeout=6000)
                res.first.click()
            except Exception:  # noqa: BLE001
                el.fill(str(v))      # free text is accepted when the suggestion list does not open
        elif a.kind == "select":
            el = self.p.locator(f'[name="{a.key}"]')
            tag = el.first.evaluate("e => e.tagName.toLowerCase()")
            if tag == "select":
                el.first.select_option(label=str(v))
            else:
                self.p.locator(f'input[name="{a.key}"][value="{_css(str(v))}"]').check()
        elif a.kind == "multiselect":
            for item in v if isinstance(v, list) else [v]:
                self.p.locator(f'input[name="{a.key}"][value="{_css(str(item))}"]').check()
        elif a.kind == "checkbox":
            box = self.p.locator(f'input[type="checkbox"][name="{a.key}"]')
            box.check() if v else box.uncheck()

    # Ashby (React form) -----------------------------------------------------------------------
    def _ashby(self, a: Answer, v: Any) -> None:
        if a.kind in ("text", "textarea", "email", "tel", "number", "date"):
            self.el(a).fill(str(v))
        elif a.kind == "location":
            box = self.entry(a).locator("input[role='combobox'], input").first
            if not self.combo(box, str(v), search=str(a.extra.get("search") or str(v).split(",")[0])):
                self.fail(a, f"{a.label[:60]} : ville introuvable dans la liste")
        elif a.kind == "boolean":
            self.entry(a).get_by_role("button", name="Yes" if v else "No", exact=True).click()
        elif a.kind in ("select", "multiselect"):
            ent = self.entry(a)
            for item in v if isinstance(v, list) else [v]:
                radio = ent.get_by_label(str(item), exact=True)
                if radio.count():
                    radio.first.check()
                    continue
                box = ent.locator("input[role='combobox']")
                if box.count() and self.combo(box.first, str(item), exact=str(item)):
                    continue
                btn = ent.get_by_role("button", name=str(item), exact=True)
                if btn.count():
                    btn.first.click()
                    continue
                self.fail(a, f"{a.label[:60]} : option « {str(item)[:40]} » introuvable")
        elif a.kind == "checkbox":
            box = self.entry(a).locator("input[type='checkbox']").first
            box.check() if v else box.uncheck()

    def verify_text(self, answers: list[Answer]) -> None:
        """Re-read every typed text field and type it again when the page lost or changed it."""
        for a in answers:
            if a.missing or a.kind not in ("text", "textarea", "email", "tel", "number", "date"):
                continue
            try:
                el = self.el(a)
                if not el.count():
                    continue
                if el.input_value().strip() != str(a.value).strip():
                    el.fill(str(a.value))
                    if el.input_value().strip() != str(a.value).strip() and a.role != "phone":
                        self.fail(a, f"{a.label[:60]} : valeur non retenue par la page")
            except Exception:  # noqa: BLE001
                continue

    # submit -----------------------------------------------------------------------------------
    def submit_button(self):  # noqa: ANN201
        if self.kind == "lever":
            btn = self.p.locator("#btn-submit, button[type='submit']")
        else:
            btn = self.p.get_by_role("button", name=re.compile(r"submit( application)?", re.I))
        return btn.first


def _css(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9@.]", "", text.lower())


def private_values(answers: list[Answer]) -> list[str]:
    """What must never leave the machine during a preview: identity, contacts and every typed answer."""
    out: set[str] = set()
    for a in answers:
        if a.missing or a.kind in ("file", "boolean", "checkbox", "select", "multiselect", "location"):
            continue
        v = _norm(str(a.value))
        if a.role == "phone":
            v = re.sub(r"\D", "", str(a.value))[-8:]
        if len(v) >= 4:
            out.add(v)
    return sorted(out)


def _guard(private: list[str], counter: list[int]):  # noqa: ANN202
    """Preview only: abort beacons and any request whose URL or body carries a private value."""
    from urllib.parse import unquote_plus

    def handler(route) -> None:  # noqa: ANN001
        req = route.request
        if req.resource_type in ("beacon", "ping"):
            counter[0] += 1
            return route.abort()
        try:
            body = req.post_data or ""
        except Exception:  # noqa: BLE001 - binary body
            body = ""
        hay = _norm(unquote_plus(req.url + " " + body))
        digits = re.sub(r"\D", "", unquote_plus(req.url + body))
        if any((p.isdigit() and p in digits) or (not p.isdigit() and p in hay) for p in private):
            counter[0] += 1
            return route.abort()
        return route.continue_()
    return handler


def fill(form_url: str, kind: str, answers: list[Answer], shot: Path, submit: bool = False,
         headless: bool = True, timeout_s: int = 45, codes=None) -> Outcome:  # noqa: ANN001
    """`codes` (submit only): .first() -> a code already given, .ask() tells the user, .wait() -> the code typed."""
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    shot.parent.mkdir(parents=True, exist_ok=True)
    clicked = False
    blocked = [0]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            try:
                ctx = browser.new_context(locale="en-US", viewport={"width": 1100, "height": 1400})
                page = ctx.new_page()
                page.set_default_timeout(timeout_s * 1000 // 3)
                if not submit:              # nothing typed may leave the machine; no socket opens at all
                    ctx.route("**/*", _guard(private_values(answers), blocked))
                    ctx.route_web_socket("**/*", lambda ws: ws.close())
                page.goto(form_url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                f = Filler(page, kind, timeout_s * 1000)
                first = next((a for a in answers if a.kind in ("text", "email") and not a.missing), None)
                if first is not None:
                    try:
                        f.el(first).wait_for(state="visible", timeout=timeout_s * 1000 // 2)
                    except PWError:
                        pass
                try:                                         # React forms reset inputs typed before hydration
                    page.wait_for_load_state("load", timeout=timeout_s * 1000 // 2)
                except PWError:
                    pass
                page.wait_for_timeout(1500)
                if submit:                                   # files first: some forms prefill from the CV
                    for a in answers:
                        if a.kind == "file":
                            f.set_one(a, attach=True)
                for a in answers:
                    if a.kind != "file":
                        f.set_one(a, attach=False)
                page.wait_for_timeout(600)
                f.verify_text(answers)
                failed = sorted(f.failed)
                if not submit:
                    page.screenshot(path=str(shot), full_page=True)
                    return Outcome("filled", shot, f.problems, failed=failed, notes=f.notes,
                                   blocked_requests=blocked[0])
                required_failed = [a.label for a in answers if a.required and a.key in f.failed]
                if required_failed:                          # never click with a required field the page refused
                    page.screenshot(path=str(shot), full_page=True)
                    return Outcome("blocked", shot, f.problems, "; ".join(required_failed)[:300], failed=failed)
                before_url = page.url
                before_hits = len(SUCCESS.findall(page.inner_text("body")))
                clicked = True
                f.submit_button().click()
                status, detail = _wait_result(page, before_url, before_hits, timeout_s, f.submit_button())
                tries = 0
                while status == "code" and codes is not None and tries < 3:
                    code = codes.first() if tries == 0 else None
                    if not code:
                        codes.ask(tries)
                        code = codes.wait()
                    if not code:
                        break
                    tries += 1
                    if not _enter_code(page, code):
                        break
                    f.submit_button().click()
                    status, detail = _wait_result(page, before_url, before_hits, timeout_s, f.submit_button(),
                                                  before_code=True)
                    if status in ("unconfirmed", "invalid") and _code_boxes(page).count() and \
                            f.submit_button().is_visible():
                        status = "code"           # code refused or expired: the form is still waiting for one
                try:
                    page.screenshot(path=str(shot), full_page=True)
                except PWError:
                    pass
                return Outcome(status, shot if shot.exists() else None, f.problems, detail, clicked=True, failed=failed)
            finally:
                browser.close()
    except PWError as exc:
        log.warning("browser run failed", extra={"error": str(exc)[:300], "clicked": clicked})
        detail = str(exc).splitlines()[0][:200]
        return Outcome("unconfirmed" if clicked else "error", None, [], detail, clicked=clicked)


def _code_boxes(page):  # noqa: ANN001, ANN202
    return page.locator(CODE_BOXES)


def _enter_code(page, code: str) -> bool:  # noqa: ANN001
    """Type the e-mailed security code: one box per character (Greenhouse), or a single field."""
    boxes = _code_boxes(page)
    n = boxes.count()
    if n == 0:
        return False
    if n >= len(code):
        for i, ch in enumerate(code):
            boxes.nth(i).fill(ch)
    else:
        boxes.first.fill(code)
    page.wait_for_timeout(500)
    return True


def _wait_result(page, before_url: str, before_hits: int, timeout_s: int, button,  # noqa: ANN001
                 before_code: bool = False) -> tuple[str, str]:
    """Submitted only on a NEW confirmation (text not on the page before the click, or a confirmation URL)."""
    deadline = time.monotonic() + max(20, timeout_s)
    while time.monotonic() < deadline:
        page.wait_for_timeout(1000)
        try:
            body = page.inner_text("body")[:30000]
        except Exception:  # noqa: BLE001 - the page is navigating
            continue
        if len(SUCCESS.findall(body)) > before_hits:
            return "submitted", page.url
        if page.url != before_url and SUCCESS_URL.search(page.url.split("?")[0]):
            return "submitted", page.url
        if not before_code and CODE_TEXT.search(body) and _code_boxes(page).count():
            return "code", ""                 # the site e-mailed a code: nothing is sent until it is typed
        if _visible_captcha(page):
            return "captcha", ""
    try:
        still_here = page.url == before_url and button.is_visible() and button.is_enabled()
        errors = page.locator("[aria-invalid='true'], .error:visible, .helper-text--error, [class*='error' i]:visible")
        texts = [t.strip() for t in errors.all_inner_texts() if t.strip()][:3]
    except Exception:  # noqa: BLE001
        still_here, texts = False, []
    if still_here and texts:                 # same page, button still there, errors shown: the site refused it
        return "invalid", " ; ".join(t[:80] for t in texts)
    return "unconfirmed", page.url
