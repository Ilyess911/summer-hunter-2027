"""Workday (phase 2, 22 Nov 2026): account per company, a 6-step wizard, and the final Submit only on the user's tap.

Unlike Greenhouse/Lever/Ashby, Workday saves each step on its servers ("Save and Continue"), so preparing an
application creates the account and a draft at the company. That is why it only starts on an explicit tap.
Every run signs in, walks the steps, fills what it can justify, and stops at: a step with unanswered required
questions ("needs"), the e-mail confirmation ("verify_email"), or the Review page ("review"). Submit is clicked
only in submit mode. The account password is generated here and kept in the macOS Keychain, never shown.

The wizard is driven by Workday's own `data-automation-id` attributes, shared by every tenant.
"""

from __future__ import annotations

import logging
import re
import secrets
import string
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from summer_hunter.apply import answers as ans
from summer_hunter.apply.ats import Field

log = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "summer-hunter-workday"
DEFAULT_ACCOUNT = "__default__"         # the password the user chose for every new account (Mini App)


def password_problem(pw: str) -> str | None:
    """Workday's usual rules, checked before saving the user's password (French message, or None)."""
    if len(pw) < 8:
        return "au moins 8 caractères"
    if not re.search(r"[A-Z]", pw) or not re.search(r"[a-z]", pw):
        return "une majuscule et une minuscule"
    if not re.search(r"\d", pw):
        return "au moins un chiffre"
    if not re.search(r"[^A-Za-z0-9]", pw):
        return "au moins un caractère spécial (! # % & * ? @)"
    if any(c in pw for c in "\"\\'\n"):
        return "sans guillemets ni barre oblique inverse"
    return None
WD_SUCCESS = re.compile(r"application (was |has been )?(successfully )?submitted|thank(s| you) for applying|"
                        r"you have (successfully )?applied", re.I)
NEXT_TEXT = re.compile(r"^\s*(next|continue|save and continue)\s*$", re.I)

# Fields Workday names the same for every company: automation id fragment -> profile role.
ROLE_BY_ID = [("legalNameSection_firstName", "first_name"), ("legalNameSection_lastName", "last_name"),
              ("legalNameSection_middleName", "skip"), ("preferredName", "skip"),
              ("addressSection_addressLine1", "address_line1"), ("addressSection_addressLine2", "skip"),
              ("addressSection_city", "city"), ("addressSection_postalCode", "postal_code"),
              ("addressSection_countryRegion", "region"), ("addressSection_regionSubdivision", "region"),
              ("countryDropdown", "country"), ("country", "country"), ("phone-device-type", "phone_device"),
              ("countryPhoneCode", "phone_country"), ("phone-number", "phone_national"),
              ("phone-extension", "skip"), ("email", "email")]
ROLE_BY_LABEL = [(r"^(legal )?(first|given) name(\(s\)|s)?$", "first_name"),
                 (r"^(legal )?((last|family) name(\(s\)|s)?|surname)$", "last_name"),
                 (r"^(full |legal )?name$", "full_name"), (r"^e-?mail( address)?$", "email"),
                 (r"^address line 1$", "address_line1"), (r"^city$", "city"),
                 (r"^(postal|zip)( code)?$", "postal_code"), (r"^country( ?/ ?(territory|region))?$", "country"),
                 (r"^phone device type$", "phone_device"), (r"^country phone code$", "phone_country"),
                 (r"^phone number$", "phone_national"), (r"^linkedin( profile| url)?$", "linkedin"),
                 (r"^(today'?s date|date)$", "today")]
# A label naming someone or something else is never the applicant's own data.
NOT_MINE = re.compile(r"referr|employer|birth|education|school|authori|emergency|reference|spouse|parent|manager", re.I)
CONSENT = re.compile(r"(privacy|data protection|personal data|data processing|privacy notice|privacy policy)", re.I)
ATTESTS = re.compile(r"certif|attest|confirm that|understand that|accurate|true|correct|complete|eligib|terms", re.I)
CONSENT_VERB = re.compile(r"\b(agree|acknowledge|consent|accept)", re.I)
SUBMIT_TEXT = re.compile(r"\bsubmit\b", re.I)

COLLECT_JS = r"""() => {
  const vis = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const out = [];
  let i = 0;
  for (const c of document.querySelectorAll('[data-automation-id^="formField-"]')) {
    if (!vis(c) || c.closest('[data-sh-skip]')) continue;
    if (c.querySelector('[data-automation-id^="formField-"]')) continue;     // only innermost fields
    const lab = c.querySelector('label, legend');
    let label = (lab ? lab.innerText : c.innerText.split('\n')[0] || '').trim();
    const required = /\*\s*$/.test(label) || !!c.querySelector('[aria-required="true"], [required]');
    label = label.replace(/\*\s*$/, '').trim();
    const id = c.getAttribute('data-automation-id').slice(10);
    const idx = 'f' + (i++);
    c.setAttribute('data-sh-idx', idx);
    let kind = 'text', value = '', options = [];
    const sel = c.querySelector('button[aria-haspopup="listbox"]');
    const multi = c.querySelector('[data-automation-id="multiselectInputContainer"], [data-uxi-widget-type="selectinput"]');
    const radios = c.querySelectorAll('input[type="radio"]');
    const boxes = c.querySelectorAll('input[type="checkbox"]');
    const month = c.querySelector('[data-automation-id="dateSectionMonth-input"]');
    const year = c.querySelector('[data-automation-id="dateSectionYear-input"]');
    const file = c.querySelector('input[type="file"]');
    if (file) { kind = 'file'; value = c.querySelector('[data-automation-id="file-upload-item"]') ? 'uploaded' : ''; }
    else if (sel) { kind = 'select'; value = sel.innerText.trim(); if (/^select one$/i.test(value)) value = ''; }
    else if (multi) { kind = 'multiselect';
      value = Array.from(c.querySelectorAll('[data-automation-id="selectedItem"]')).map(x => x.innerText.trim()).join(' | '); }
    else if (radios.length) { kind = 'select';
      options = Array.from(radios).map(r => (c.querySelector('label[for="' + r.id + '"]') || r.parentElement).innerText.trim());
      const on = Array.from(radios).findIndex(r => r.checked); value = on >= 0 ? options[on] : ''; }
    else if (month || year) { kind = 'date';
      value = [month && month.value, year && year.value].filter(Boolean).join('/'); }
    else if (boxes.length === 1) { kind = 'checkbox'; value = boxes[0].checked ? 'true' : ''; }
    else { const t = c.querySelector('textarea, input:not([type="hidden"])');
      if (!t || t.getAttribute('data-automation-id') === 'beecatcher') continue;      // honeypot: never touched
      kind = t.tagName === 'TEXTAREA' ? 'textarea' : 'text'; value = t.value || ''; }
    out.push({idx, id, label, required, kind, value, options});
  }
  return out;
}"""


@dataclass
class WDField:
    idx: str
    id: str
    label: str
    required: bool
    kind: str
    value: str
    options: list[str] = field(default_factory=list)


@dataclass
class WDResult:
    status: str              # review | needs | verify_email | account_exists | invalid | submitted | unconfirmed | captcha | error
    screenshot: Path | None = None
    step: str = ""
    questions: list[ans.Answer] = field(default_factory=list)    # unanswered required fields of the current step
    filled: list[ans.Answer] = field(default_factory=list)       # what this run typed, for the summary
    detail: str = ""
    clicked: bool = False


# Keychain -----------------------------------------------------------------------------------------

def account_name(tenant_host: str, email: str) -> str:
    return f"{tenant_host}:{email.lower()}"


def keychain_get(account: str) -> str | None:
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


class KeychainError(Exception):
    pass


def keychain_set(account: str, password: str) -> None:
    """The secret goes through stdin (`security -i`), never in a process argument or an error message."""
    if any(c in password for c in "\"\\\n'") or any(c in account for c in "\"\\\n"):
        raise KeychainError("unsupported character")
    cmd = f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a "{account}" -w "{password}"\n'
    try:
        r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, check=False, timeout=20)
    except (OSError, subprocess.SubprocessError):
        raise KeychainError("keychain unavailable") from None
    if r.returncode != 0 or keychain_get(account) != password:
        raise KeychainError("keychain write failed")


def keychain_delete(account: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
                   capture_output=True, text=True, check=False)


def new_password() -> str:
    """16 characters with every class Workday asks for (upper, lower, digit, special)."""
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!#%&*?@"]
    chars = [secrets.choice(p) for p in pools] + [secrets.choice("".join(pools)) for _ in range(12)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


# Answers ------------------------------------------------------------------------------------------

def norm_label(label: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]", " ", label.lower())).strip()[:160]


def role_for(f: WDField) -> str:
    if NOT_MINE.search(f.label) or NOT_MINE.search(f.id):
        return ""
    for frag, role in ROLE_BY_ID:
        if f.id == frag:                       # exact Workday ids only: a prefixed id belongs to another section
            return role
    label = f.label.strip().rstrip(":").strip()
    for rx, role in ROLE_BY_LABEL:
        if re.search(rx, label, re.I):
            return role
    return ""


def profile_value(role: str, who: ans.Applicant, cfg: dict[str, Any]) -> str | None:
    a = cfg.get("applicant") or {}
    phone = re.sub(r"[^\d]", "", who.phone)
    national = phone[2:] if phone.startswith("33") else phone
    vals = {"country": a.get("country"), "address_line1": a.get("address_line1"), "city": a.get("city"),
            "postal_code": a.get("postal_code"), "region": a.get("region"), "phone_device": a.get("phone_device", "Mobile"),
            "phone_country": a.get("phone_country", "France (+33)"), "phone_national": national or None,
            "today": date.today().strftime("%m/%d/%Y")}
    if role in vals:
        return vals[role] or None
    return who.value(role)


def resolve(f: WDField, known: dict[str, Any], who: ans.Applicant, cfg: dict[str, Any], citizenship: list[str],
            company: str) -> ans.Answer:
    """What goes in one Workday field: the user's earlier answer, the profile, a rule, else empty (asked later)."""
    kind = {"select": "select", "multiselect": "multiselect", "date": "text", "checkbox": "checkbox"}.get(f.kind, "text")
    a = ans.Answer(norm_label(f.label), f.label, kind, f.required, list(f.options), "", extra={"wd_kind": f.kind})
    if a.key in known:                                      # the user answered this exact question before
        a.value, a.source = known[a.key], ans.USER
        return a
    role = role_for(f)
    a.role = role
    if role == "skip":
        a.source, a.note = ans.PROFILE, "laissé vide"
        return a
    if role and (v := profile_value(role, who, cfg)):
        if f.options:                                    # dropdowns: the site's own wording of the same thing
            pats = {"phone_device": [r"^mobile", r"mobile", r"cell"], "country": [rf"^{re.escape(v)}$"],
                    "phone_country": [rf"^{re.escape(v.split(' (')[0])}\b"]}.get(role, [])
            v = ans.match_option(f.options, v) or ans._pick(f.options, pats)
            if v is None:
                return a                                 # not in the list: asked rather than forced
        a.value, a.source = v, ans.PROFILE
        return a
    if f.kind == "multiselect" and re.search(r"how did you (hear|find|learn)|source", f.label, re.I):
        alts = (cfg.get("applicant") or {}).get("heard_from") or ["Company Website", "Career Site", "Careers",
                                                                   "Website", "LinkedIn", "Job Board", "Other"]
        a.value, a.source, a.extra["alts"] = alts[0], ans.RULE, list(alts)
        return a
    fld = Field(a.key, f.label, kind if kind != "checkbox" else "checkbox", f.required, list(f.options))
    for fn in (lambda: ans._availability(f.label, f.options, kind),
               lambda: ans._work_auth(f.label, f.options, citizenship),
               lambda: ans._rule(cfg.get("rules") or [], fld)):
        got = fn()
        if got:
            a.value, a.note = got
            a.source = ans.RULE if a.value is not None else ans.ASK
            return a
    if kind == "checkbox" and CONSENT.search(f.label) and CONSENT_VERB.search(f.label) and not ATTESTS.search(f.label):
        a.value, a.source = True, ans.RULE         # data-processing consent only; any attestation is asked
    return a


# Browser ------------------------------------------------------------------------------------------

SINGLE_PROFILE = re.compile(r"only one (authentic )?(candidate )?(profile|account)|duplicate (candidate )?(accounts?|profiles?)",
                            re.I)


def plus_alias(email: str, tenant_host: str) -> str | None:
    """A fresh address for a new account that still lands in the same Gmail inbox (name+tenant@gmail.com)."""
    local, _, domain = email.partition("@")
    if domain.lower() not in ("gmail.com", "googlemail.com") or not local:
        return None
    tag = re.sub(r"[^a-z0-9]", "", tenant_host.split(".")[0].lower())[:20] or "wd"
    return f"{local.split('+')[0]}+{tag}@{domain}"


class Driver:
    def __init__(self, page, timeout_s: int) -> None:  # noqa: ANN001
        self.p, self.t = page, timeout_s
        self.single_profile = False          # the site forbids several accounts per person

    def aid(self, name: str):  # noqa: ANN201
        return self.p.locator(f'[data-automation-id="{name}"]')

    def click(self, name: str) -> bool:
        loc = self.aid(name)
        if not loc.count() or not loc.first.is_visible():
            return False
        try:
            loc.first.click(timeout=8000)
        except Exception:  # noqa: BLE001 - Workday lays a click_filter div over its buttons
            loc.first.click(force=True)
        return True

    def wait_any(self, names: list[str], timeout_s: float = 15) -> str | None:
        """Wait until one of these automation ids is visible (Workday renders its pages late)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for n in names:
                loc = self.aid(n)
                try:
                    if loc.count() and loc.first.is_visible():
                        return n
                except Exception:  # noqa: BLE001
                    pass
            self.p.wait_for_timeout(500)
        return None

    def accept_cookies(self) -> None:
        self.click("legalNoticeAcceptButton")

    def wait(self, ms: int = 1500) -> None:
        self.p.wait_for_timeout(ms)

    def step(self) -> str:
        loc = self.aid("progressBarActiveStep")
        try:
            return re.sub(r"^current step \d+ of \d+\s*", "", loc.first.inner_text(timeout=5000)).strip()
        except Exception:  # noqa: BLE001
            return ""

    def errors(self) -> list[str]:
        loc = self.p.locator('[data-automation-id="errorMessage"], [data-automation-id="inputAlert"], '
                             '[data-automation-id="errorBanner"]')
        try:
            return [t.strip() for t in loc.all_inner_texts() if t.strip()][:5]
        except Exception:  # noqa: BLE001
            return []

    def fields(self) -> list[WDField]:
        return [WDField(**d) for d in self.p.evaluate(COLLECT_JS)]

    def box(self, f: WDField):  # noqa: ANN201
        return self.p.locator(f'[data-sh-idx="{f.idx}"]')

    def listbox(self, btn):  # noqa: ANN001, ANN201
        """The option list this dropdown button opened (not another one still on screen)."""
        ctrl = btn.get_attribute("aria-controls") or ""
        if ctrl:
            return self.p.locator(f'[id="{ctrl}"]')
        return self.p.locator('[role="listbox"]:visible').last

    def options_of(self, f: WDField) -> list[str]:
        if f.kind != "select" or f.options:
            return f.options
        self.p.keyboard.press("Escape")
        self.wait(300)
        btn = self.box(f).locator('button[aria-haspopup="listbox"]').first
        try:
            btn.click()
            self.wait(600)
            opts = self.listbox(btn).locator('[role="option"]')
            opts.first.wait_for(state="visible", timeout=5000)
            texts = [t.strip() for t in opts.all_inner_texts() if t.strip()]
        except Exception:  # noqa: BLE001
            texts = []
        self.p.keyboard.press("Escape")
        self.wait(300)
        return [t for t in texts if not re.fullmatch(r"select one", t, re.I)]

    def put(self, f: WDField, a: ans.Answer) -> bool:
        v = a.value
        c = self.box(f)
        try:
            if f.kind == "file":
                c.locator('input[type="file"]').set_input_files(str(v))
                self.wait(4000)
            elif f.kind == "select" and c.locator('input[type="radio"]').count():
                c.get_by_label(str(v), exact=True).check()
            elif f.kind == "select":
                self.p.keyboard.press("Escape")
                btn = c.locator('button[aria-haspopup="listbox"]').first
                btn.click()
                self.wait(600)
                opt = self.listbox(btn).locator('[role="option"]').filter(
                    has_text=re.compile(rf"^\s*{re.escape(str(v))}\s*$", re.I))
                opt.first.click(timeout=6000)
            elif f.kind == "multiselect":
                items = v if isinstance(v, list) else [v]
                alts = list(a.extra.get("alts") or [])
                if alts and not isinstance(v, list):     # several ways to say it: the first the site offers wins
                    for alt in alts:
                        if self._prompt_pick(c, alt):
                            return True
                    return False
                for item in items:
                    box = c.locator("input").first
                    box.click()
                    box.fill(str(item))
                    self.wait(1500)
                    opts = self.p.locator('[data-automation-id="promptOption"]:visible, [role="option"]:visible')
                    texts = [t.strip().lower() for t in opts.all_inner_texts()]
                    if not texts:                      # some prompts search only on Enter; nothing is highlighted yet
                        box.press("Enter")
                        self.wait(1500)
                        texts = [t.strip().lower() for t in opts.all_inner_texts()]
                    want = str(item).strip().lower()
                    i = next((k for k, t in enumerate(texts) if t == want), None)
                    if i is None:
                        i = next((k for k, t in enumerate(texts) if want in t), None)
                    if i is None:
                        self.p.keyboard.press("Escape")
                        return False
                    opts.nth(i).click()
                    self.wait(600)
                    self.p.keyboard.press("Escape")
            elif f.kind == "date":
                m = re.search(r"(\d{1,2})[/ -](\d{4})", str(v)) or re.search(r"([A-Za-z]+) (\d{4})", str(v))
                if not m:
                    return False
                month = m.group(1)
                if not month.isdigit():
                    month = str(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
                                .index(month[:3].lower()) + 1)
                mi = c.locator('[data-automation-id="dateSectionMonth-input"]')
                if mi.count():
                    mi.first.click()
                    mi.first.type(month.zfill(2))
                c.locator('[data-automation-id="dateSectionYear-input"]').first.type(m.group(2))
            elif f.kind == "checkbox":
                box = c.locator('input[type="checkbox"]').first
                box.check() if v else box.uncheck()
            else:
                t = c.locator("textarea, input:not([type='hidden'])").first
                t.fill(str(v))
                t.press("Tab")
            self.wait(400)
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("workday field failed", extra={"field": f.label[:60], "error": str(exc)[:200]})
            self.p.keyboard.press("Escape")
            return False

    def _prompt_pick(self, c, item: str) -> bool:  # noqa: ANN001
        box = c.locator("input").first
        box.click()
        box.fill(item)
        box.press("Enter")
        self.wait(1500)
        opts = self.p.locator('[data-automation-id="promptOption"]:visible, [role="option"]:visible')
        texts = [t.strip().lower() for t in opts.all_inner_texts()]
        want = item.lower()
        i = next((k for k, t in enumerate(texts) if want in t), None)
        if i is None:
            box.fill("")
            self.p.keyboard.press("Escape")
            return False
        opts.nth(i).click()
        self.wait(800)
        self.p.keyboard.press("Escape")
        return bool(c.locator('[data-automation-id="selectedItem"]').count())

    def next_button(self):  # noqa: ANN201
        return self.p.locator('[data-automation-id="pageFooterNextButton"], [data-automation-id="bottom-navigation-next-button"]').first


def _shot(page, path: Path) -> Path | None:  # noqa: ANN001
    try:
        page.screenshot(path=str(path), full_page=True)
        return path
    except Exception:  # noqa: BLE001
        return None


def review_fingerprint(page) -> str:  # noqa: ANN001
    """What the Review page says (answers, files), to check at submit time that it is what the user approved."""
    import hashlib

    try:
        main = page.locator("[data-automation-id='applyFlowPage'], main").first.inner_text(timeout=5000)
    except Exception:  # noqa: BLE001
        main = page.inner_text("body")
    text = re.sub(r"\s+", " ", main).strip().lower()
    return hashlib.sha256(text.encode()).hexdigest()


def run(form_url: str, email: str, account: str, known: dict[str, Any], who: ans.Applicant, cfg: dict[str, Any],
        citizenship: list[str], company: str, resume: str | None, shot: Path, submit: bool = False,
        headless: bool = True, timeout_s: int = 45, model=None, approved_fp: str | None = None) -> WDResult:  # noqa: ANN001
    """One pass through the wizard. `model(answers)` may answer custom questions (it fills Answer values)."""
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    shot.parent.mkdir(parents=True, exist_ok=True)
    clicked = False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            try:
                page = browser.new_context(locale="en-US", viewport={"width": 1200, "height": 1400}).new_page()
                page.set_default_timeout(timeout_s * 1000 // 2)
                page.goto(form_url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                d = Driver(page, timeout_s)
                d.wait(5000)
                d.accept_cookies()
                auth = _authenticate(d, email, account)
                if auth != "ok":
                    if not d.single_profile:
                        try:
                            d.single_profile = bool(SINGLE_PROFILE.search(page.inner_text("body")))
                        except Exception:  # noqa: BLE001
                            pass
                    return WDResult(auth, _shot(page, shot), d.step(), detail="single_profile" if d.single_profile else "")
                filled: list[ans.Answer] = []
                seen_steps: list[str] = []
                for _ in range(10):
                    d.wait(2500)
                    step = d.step()
                    btn = d.next_button()
                    try:
                        btn_text = btn.inner_text(timeout=5000).strip()
                    except Exception:  # noqa: BLE001
                        return WDResult("invalid", _shot(page, shot), step, filled=filled,
                                        detail="bouton de navigation illisible : arrêt par prudence")
                    on_review = bool(re.search(r"review", step, re.I)) or bool(SUBMIT_TEXT.search(btn_text))
                    if on_review:
                        fp = review_fingerprint(page)
                        if not submit:
                            res = WDResult("review", _shot(page, shot), step or "Review", filled=filled)
                            res.detail = fp
                            return res
                        if approved_fp is None or fp != approved_fp:     # not the page the user approved
                            res = WDResult("review", _shot(page, shot), step or "Review", filled=filled)
                            res.detail = fp
                            res.questions = []
                            res.step = "changed"
                            return res
                        before = (page.url, len(WD_SUCCESS.findall(page.inner_text("body"))))
                        clicked = True
                        btn.click()
                        return _after_submit(d, page, shot, step, filled, before)
                    if not step:                                  # unknown page: never click blindly
                        return WDResult("invalid", _shot(page, shot), "", filled=filled, detail="page Workday non reconnue")
                    if seen_steps.count(step) >= 2:
                        return WDResult("invalid", _shot(page, shot), step, filled=filled,
                                        detail=" ; ".join(d.errors()) or "étape bloquée")
                    seen_steps.append(step)
                    if not NEXT_TEXT.match(btn_text):            # only a plain Next/Continue is ever clicked here
                        return WDResult("invalid", _shot(page, shot), step, filled=filled,
                                        detail=f"bouton inattendu « {btn_text[:40]} » : arrêt par prudence")
                    if submit:                                    # submit mode never types: the draft must be complete
                        missing = [f for f in d.fields() if f.required and not f.value and f.kind != "file"]
                        if missing:
                            return WDResult("needs", _shot(page, shot), step, filled=filled,
                                            detail="le brouillon a changé depuis l'aperçu")
                    else:
                        pending = _fill_step(d, known, who, cfg, citizenship, company, resume, filled, model)
                        if pending:
                            return WDResult("needs", _shot(page, shot), step, pending, filled)
                    btn.click()
                    d.wait(3000)
                    if errs := d.errors():
                        return WDResult("invalid", _shot(page, shot), step, filled=filled, detail=" ; ".join(errs))
                return WDResult("invalid", _shot(page, shot), d.step(), filled=filled, detail="trop d'étapes")
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - the outcome must always say whether the final click happened
        log.warning("workday run failed", extra={"error": type(exc).__name__, "clicked": clicked})
        detail = str(exc).splitlines()[0][:200] if isinstance(exc, PWError) else type(exc).__name__
        return WDResult("unconfirmed" if clicked else "error", None, detail=detail, clicked=clicked)


AUTH_STEP = re.compile(r"create account|sign in", re.I)


def _on_auth_page(d: Driver) -> bool:
    if AUTH_STEP.search(d.step()):
        return True
    return any(d.aid(n).count() for n in ("signInSubmitButton", "createAccountSubmitButton", "SignInWithEmailButton"))


AUTH_IDS = ["SignInWithEmailButton", "signInSubmitButton", "createAccountSubmitButton", "createAccountLink",
            "signInLink", "pageFooterNextButton", "bottom-navigation-next-button"]


def _sign_in(d: Driver, email: str, password: str) -> str:
    if d.wait_any(["SignInWithEmailButton", "signInSubmitButton", "signInLink"]) == "SignInWithEmailButton":
        d.click("SignInWithEmailButton")             # some tenants first offer Google / LinkedIn / e-mail
        d.wait_any(["signInSubmitButton"])
    if not d.aid("signInSubmitButton").count():
        d.click("signInLink")
        d.wait_any(["signInSubmitButton"])
    if not d.aid("signInSubmitButton").count():
        return "error"
    d.aid("email").first.fill(email)
    d.aid("password").first.fill(password)
    d.click("signInSubmitButton")
    d.wait(5000)
    body = d.p.inner_text("body")
    if re.search(r"verif(y|ication)|not (yet )?verified|activate your account", body, re.I) and d.aid("email").count():
        return "verify_email"
    if _on_auth_page(d):
        return "auth_failed"                         # wrong password: never retried automatically
    return "ok"


def _authenticate(d: Driver, email: str, account: str) -> str:
    """Sign in with the stored password, or create the account (password stored BEFORE the click)."""
    first = d.wait_any(AUTH_IDS)
    if first == "SignInWithEmailButton":
        d.click("SignInWithEmailButton")
        d.wait_any(["signInSubmitButton", "createAccountLink", "createAccountSubmitButton"])
    if not _on_auth_page(d):
        return "ok"
    password = keychain_get(account)
    if password is not None:
        return _sign_in(d, email, password)
    default = keychain_get(DEFAULT_ACCOUNT)              # the user's chosen password, if set
    if not d.aid("verifyPassword").count():
        d.click("createAccountLink")
        d.wait_any(["verifyPassword"])
    if not d.aid("verifyPassword").count():
        return "error"
    password = default or new_password()
    keychain_set(account, password)
    d.aid("email").first.fill(email)
    d.aid("password").first.fill(password)
    d.aid("verifyPassword").first.fill(password)
    box = d.aid("createAccountCheckbox")
    if box.count():
        label = next((f.label for f in d.fields() if f.kind == "checkbox"), "")
        try:                                         # the wording often sits above a bare "I agree" box
            label += " " + d.aid("signInContent").first.inner_text(timeout=5000)
        except Exception:  # noqa: BLE001
            pass
        # Account creation consent (profile storage, privacy notice) is what the user asked for; anything else
        # (no privacy wording at all) is never accepted.
        if not (CONSENT.search(label) and CONSENT_VERB.search(label)):
            keychain_delete(account)
            return "error"
        box.first.check()
    try:
        d.single_profile = bool(SINGLE_PROFILE.search(d.aid("signInContent").first.inner_text(timeout=3000)))
    except Exception:  # noqa: BLE001
        pass
    d.click("createAccountSubmitButton")
    d.wait(5000)
    body = d.p.inner_text("body")
    if re.search(r"already (exists|in use)|account with this email", body, re.I):
        if default:                                  # maybe created earlier with the same chosen password
            res = _sign_in(d, email, default)
            if res in ("ok", "verify_email"):
                return res
        keychain_delete(account)                     # the password stored above is not that account's
        return "account_exists"
    if re.search(r"verif(y|ication)|confirm your (email|account)|activate your account", body, re.I) and \
            _on_auth_page(d):
        return "verify_email"
    if d.aid("verifyPassword").count():              # creation refused: the stored password is not valid
        keychain_delete(account)
        return "error"
    if _on_auth_page(d):                             # Workday sends some tenants back to Sign In after creation
        res = _sign_in(d, email, password)
        # A refusal right after creating the account means it still needs its e-mail confirmed (NVIDIA, 22 Nov
        # 2026): ask for the confirmation instead of reporting a wrong password.
        return "verify_email" if res == "auth_failed" else res
    return "ok"


def _fill_step(d: Driver, known: dict[str, Any], who: ans.Applicant, cfg: dict[str, Any], citizenship: list[str],
               company: str, resume: str | None, filled: list[ans.Answer], model) -> list[ans.Answer]:  # noqa: ANN001
    pending: list[ans.Answer] = []
    todo: list[tuple[WDField, ans.Answer]] = []
    for f in d.fields():
        if f.kind == "file":
            if not f.value and resume:
                a = ans.Answer("resume", f.label or "Resume", "file", f.required, [], "resume", resume, ans.FILE)
                if d.put(f, a):
                    filled.append(a)
            continue
        if f.value:                                          # Workday kept it from a previous run or the resume
            continue
        f.options = d.options_of(f)
        a = resolve(f, known, who, cfg, citizenship, company)
        todo.append((f, a))
    if model is not None:
        model([a for _, a in todo if a.missing and a.required and a.source != ans.PROFILE])
    for f, a in todo:
        if a.missing:
            if a.required:
                pending.append(a)
            continue
        if d.put(f, a):
            filled.append(a)
        elif a.required:
            a.note = "refusé par la page"
            pending.append(a)
    return pending


def _after_submit(d: Driver, page, shot: Path, step: str, filled: list[ans.Answer],  # noqa: ANN001
                  before: tuple[str, int]) -> WDResult:
    """Submitted only on a NEW confirmation, once the wizard (its progress bar) is gone."""
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        d.wait(1500)
        try:
            body = page.inner_text("body")[:30000]
        except Exception:  # noqa: BLE001
            continue
        if len(WD_SUCCESS.findall(body)) > before[1] and not d.aid("progressBarActiveStep").count():
            return WDResult("submitted", _shot(page, shot), step, filled=filled, clicked=True)
    return WDResult("unconfirmed", _shot(page, shot), step, filled=filled, clicked=True,
                    detail=" ; ".join(d.errors()))
