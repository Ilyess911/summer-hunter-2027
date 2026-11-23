"""Which applicant tracking system hosts a posting, and what its form asks (read through public endpoints).

Greenhouse: the public Job Board API (`?questions=true`). Ashby: the GraphQL endpoint its own job page calls.
Lever: the server-rendered /apply page (custom questions are JSON in a hidden `baseTemplate` input).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36"

_GH = re.compile(r"^https://(?:job-boards(?:\.eu)?|boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)")
_GH_EMBED = re.compile(r"^https://(?:job-boards(?:\.eu)?|boards)\.greenhouse\.io/embed/job_app\?(?=.*for=([\w-]+))(?=.*token=(\d+))")
_LEVER = re.compile(r"^https://jobs\.(eu\.)?lever\.co/([\w.-]+)/([0-9a-f-]{36})")
_ASHBY = re.compile(r"^https://jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})")

_WORKDAY = re.compile(r"^https://([\w-]+)\.(wd\d{1,3})\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)/job/"
                      r"([^?#]+?)(?:/apply(?:/[^?#]*)?)?/?(?:[?#].*)?$")

SUPPORTED = ("greenhouse", "lever", "ashby", "workday")
NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday"}


@dataclass
class Target:
    kind: str           # greenhouse | lever | ashby
    board: str
    job_id: str
    form_url: str
    eu: bool = False


@dataclass
class Field:
    key: str            # DOM id (Greenhouse, Ashby) or input name (Lever)
    label: str
    kind: str           # text textarea email tel file select multiselect boolean checkbox location number
    required: bool = False
    options: list[str] = field(default_factory=list)
    role: str = ""      # what the value is about: first_name, resume, linkedin, consent ... ("" = custom question)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Form:
    target: Target
    title: str
    fields: list[Field]

    def to_dict(self) -> dict[str, Any]:
        return {"target": asdict(self.target), "title": self.title, "fields": [asdict(f) for f in self.fields]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Form:
        return cls(Target(**d["target"]), d.get("title", ""), [Field(**f) for f in d.get("fields", [])])


class FormError(Exception):
    pass


_GH_JID = re.compile(r"[?&#]gh_jid=(\d+)")
_GH_BOARD_IN_PAGE = re.compile(r"greenhouse\.io/(?:embed/job_app\?(?:[^\"'\s]*&)?for=|embed/job_board\?for=)?([a-z0-9][\w-]{1,60})")


_ANY_URL = re.compile(r"https://[^\s<>\"')\]]+")


def best_url(text: str | None) -> str | None:
    """In pasted offer text: the first link the bot can apply through (ATS form or company page with gh_jid)."""
    for u in _ANY_URL.findall(text or ""):
        u = u.rstrip(".,;:")
        if detect(u) or gh_jid(u):
            return u
    return None


def gh_jid(url: str | None) -> str | None:
    """Greenhouse job id carried by a company careers page (stripe.com/jobs/...?gh_jid=123)."""
    m = _GH_JID.search(url or "")
    return m.group(1) if m else None


def board_from_page(url: str, client: httpx.Client | None = None) -> str | None:
    """The Greenhouse board a company page embeds (its HTML names job-boards.greenhouse.io/<board>)."""
    own = client is None
    client = client or _client()
    try:
        r = client.get(url)
        bad = {"embed", "v1", "boards", "job_app", "jobs", "assets", "static", "favicon"}
        for b in _GH_BOARD_IN_PAGE.findall(r.text if r.status_code == 200 else ""):
            if b.lower() not in bad:
                return b
    except httpx.HTTPError:
        return None
    finally:
        if own:
            client.close()
    return None


def detect(url: str | None, gh_board: str | None = None) -> Target | None:
    u = (url or "").strip()
    if gh_board and (jid := gh_jid(u)) and u.startswith("https://"):
        return Target("greenhouse", gh_board, jid, f"https://job-boards.greenhouse.io/{gh_board}/jobs/{jid}")
    if m := _GH.match(u):
        eu = ".eu." in u
        host = "job-boards.eu.greenhouse.io" if eu else "job-boards.greenhouse.io"
        return Target("greenhouse", m.group(1), m.group(2), f"https://{host}/{m.group(1)}/jobs/{m.group(2)}", eu)
    if m := _GH_EMBED.match(u):
        return Target("greenhouse", m.group(1), m.group(2), f"https://job-boards.greenhouse.io/{m.group(1)}/jobs/{m.group(2)}")
    if m := _LEVER.match(u):
        eu = bool(m.group(1))
        return Target("lever", m.group(2), m.group(3),
                      f"https://jobs.{'eu.' if eu else ''}lever.co/{m.group(2)}/{m.group(3)}/apply", eu)
    if m := _ASHBY.match(u):
        return Target("ashby", m.group(1), m.group(2), f"https://jobs.ashbyhq.com/{m.group(1)}/{m.group(2)}/application")
    if m := _WORKDAY.match(u):
        host = f"{m.group(1)}.{m.group(2)}.myworkdayjobs.com"
        return Target("workday", host, m.group(4),
                      f"https://{host}/en-US/{m.group(3)}/job/{m.group(4)}/apply/autofillWithResume")
    return None


def _client() -> httpx.Client:
    return httpx.Client(timeout=25, headers={"User-Agent": UA}, follow_redirects=True)


def fetch_form(target: Target, client: httpx.Client | None = None) -> Form:
    own = client is None
    client = client or _client()
    try:
        if target.kind == "greenhouse":
            return _greenhouse(target, client)
        if target.kind == "lever":
            return _lever(target, client)
        if target.kind == "ashby":
            return _ashby(target, client)
        raise FormError(f"unsupported platform {target.kind}")
    except httpx.HTTPError as exc:
        raise FormError(f"form unreachable ({type(exc).__name__})") from None
    finally:
        if own:
            client.close()


# Greenhouse -------------------------------------------------------------------------------------

_GH_ROLES = {"first_name": "first_name", "last_name": "last_name", "preferred_name": "preferred_name", "email": "email",
             "phone": "phone", "resume": "resume", "cover_letter": "cover_letter"}
_GH_KIND = {"input_text": "text", "textarea": "textarea", "input_file": "file",
            "multi_value_single_select": "select", "multi_value_multi_select": "multiselect"}


def _greenhouse(t: Target, c: httpx.Client) -> Form:
    # One API host serves both regions (boards-api.eu.greenhouse.io does not resolve; checked 22 Nov 2026).
    r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{t.board}/jobs/{t.job_id}?questions=true")
    if r.status_code == 404:
        raise FormError("offer closed or removed")
    r.raise_for_status()
    d = r.json()
    fields: list[Field] = []
    for q in d.get("questions") or []:
        fs = [f for f in q.get("fields", []) if f.get("type") != "input_hidden"]
        if not fs:
            continue
        f = next((x for x in fs if x["type"] == "input_file"), fs[0])     # resume: file, not the pasted text
        kind = _GH_KIND.get(f["type"], "text")
        opts = [str(v.get("label", "")) for v in f.get("values") or []]
        label = _clean(q.get("label"))
        fields.append(Field(f["name"], label, kind, bool(q.get("required")), opts,
                            _GH_ROLES.get(f["name"]) or _role_from_label(label, kind),
                            {"description": _clean(q.get("description"))[:400]}))
    for q in d.get("location_questions") or []:
        for f in q.get("fields", []):
            if f.get("name") == "location":
                fields.append(Field("candidate-location", "Location (City)", "location", bool(q.get("required")),
                                    role="location"))
    edu = str(d.get("education") or "")
    if edu in ("education_required", "education_optional"):
        req = edu == "education_required"
        fields += [Field("school--0", "School", "select", req, role="edu_school"),
                   Field("degree--0", "Degree", "select", req, role="edu_degree"),
                   Field("end-month--0", "End date month", "select", req, role="edu_end_month"),
                   Field("end-year--0", "End date year", "number", req, role="edu_end_year")]
    demo = d.get("demographic_questions") or {}
    for q in (demo.get("questions") or []) if isinstance(demo, dict) else []:
        fields.append(Field(f"demographic::{q.get('id')}", _clean(q.get("label")), "select", bool(q.get("required")),
                            [str(o.get("label", "")) for o in q.get("answer_options") or []], "demographic"))
    for comp in d.get("compliance") or []:
        for q in comp.get("questions") or []:
            for f in q.get("fields", []):
                if f.get("type") == "multi_value_single_select":
                    fields.append(Field(f["name"], _clean(q.get("label")), "select", bool(q.get("required")),
                                        [str(v.get("label", "")) for v in f.get("values") or []], "demographic"))
    return Form(t, d.get("title", ""), fields)


# Lever ------------------------------------------------------------------------------------------

_LEVER_STD = [("name", "Full name", "text", "full_name"), ("email", "Email", "email", "email"),
              ("phone", "Phone", "tel", "phone"), ("location", "Current location", "location", "location"),
              ("org", "Current company", "text", "current_company"), ("urls[LinkedIn]", "LinkedIn URL", "text", "linkedin"),
              ("urls[GitHub]", "GitHub URL", "text", "github"), ("urls[Portfolio]", "Portfolio URL", "text", "website"),
              ("urls[Twitter]", "Twitter URL", "text", "twitter"), ("urls[Other]", "Other website", "text", "other_url")]
_LEVER_CARD_KIND = {"multiple-choice": "select", "multiple-select": "multiselect", "text": "text",
                    "textarea": "textarea", "dropdown": "select", "file-upload": "file"}


def _lever(t: Target, c: httpx.Client) -> Form:
    r = c.get(t.form_url)
    if r.status_code == 404:
        raise FormError("offer closed or removed")
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = _clean((soup.select_one(".posting-header h2") or soup.select_one("h2") or soup.new_tag("x")).get_text())
    present = {i.get("name") for i in soup.select("input[name], textarea[name], select[name]")}

    def required(name: str) -> bool:
        el = soup.select_one(f'[name="{name}"]')
        return bool(el and el.has_attr("required"))

    fields: list[Field] = [Field("resume", "Resume/CV", "file", True, role="resume")]
    for key, label, kind, role in _LEVER_STD:
        if key in present:
            fields.append(Field(key, label, kind, required(key), role=role))
    for inp in soup.select('input[name$="[baseTemplate]"]'):
        card = re.match(r"cards\[([^\]]+)\]", inp["name"])
        try:
            tpl = json.loads(html.unescape(inp.get("value", "")))
        except ValueError:
            continue
        for i, f in enumerate(tpl.get("fields", [])):
            kind = _LEVER_CARD_KIND.get(f.get("type"), "text")
            label = _clean(f.get("text"))
            fields.append(Field(f"cards[{card.group(1)}][field{i}]", label, kind, bool(f.get("required")),
                                [str(o.get("text", "")) for o in f.get("options") or []], _role_from_label(label, kind),
                                {"description": _clean(f.get("description"))[:400]}))
    for sel in soup.select('select[name^="eeo["]'):
        label = {"eeo[gender]": "Gender", "eeo[race]": "Race", "eeo[veteran]": "Veteran status",
                 "eeo[disability]": "Disability status"}.get(sel["name"], sel["name"])
        opts = [_clean(o.get_text()) for o in sel.select("option") if o.get("value")]
        fields.append(Field(sel["name"], label, "select", sel.has_attr("required"), opts, "demographic"))
    for cb in soup.select('input[type="checkbox"][name^="consent["]'):
        fields.append(Field(cb["name"], _clean(_lever_label(cb)) or cb["name"], "checkbox", cb.has_attr("required"),
                            role="consent" if cb["name"] == "consent[store]" else "marketing"))
    if "comments" in present:
        fields.append(Field("comments", "Additional information", "textarea", False, role="additional_info"))
    return Form(t, title, fields)


def _lever_label(el) -> str:  # noqa: ANN001
    lab = el.find_parent("label")
    return lab.get_text(" ") if lab else ""


# Ashby ------------------------------------------------------------------------------------------

_ASHBY_Q = ("query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { jobPosting("
            "organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { id title "
            "applicationForm { sections { title fieldEntries { ... on FormFieldEntry { id field isRequired } } } } } }")
_ASHBY_KIND = {"String": "text", "Email": "email", "Phone": "tel", "File": "file", "LongText": "textarea",
               "Boolean": "boolean", "ValueSelect": "select", "MultiValueSelect": "multiselect", "Location": "location",
               "Number": "number", "Date": "date", "SocialLink": "text"}
_ASHBY_SYS = {"_systemfield_name": "full_name", "_systemfield_email": "email", "_systemfield_resume": "resume",
              "_systemfield_location": "location", "_systemfield_phone": "phone"}


def _ashby(t: Target, c: httpx.Client) -> Form:
    r = c.post("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting",
               json={"operationName": "ApiJobPosting", "query": _ASHBY_Q,
                     "variables": {"organizationHostedJobsPageName": t.board, "jobPostingId": t.job_id}})
    r.raise_for_status()
    jp = (r.json().get("data") or {}).get("jobPosting")
    if not jp:
        raise FormError("offer closed or removed")
    fields: list[Field] = []
    for s in (jp.get("applicationForm") or {}).get("sections") or []:
        for e in s.get("fieldEntries") or []:
            f = e.get("field") or {}
            path, title = str(f.get("path", "")), _clean(f.get("title"))
            kind = _ASHBY_KIND.get(str(f.get("type")), "text")
            role = _ASHBY_SYS.get(path, "")
            if not role:
                role = _role_from_label(title, kind)
            opts = [str(v.get("label", "")) for v in f.get("selectableValues") or []]
            fields.append(Field(path, title, kind, bool(e.get("isRequired")), opts, role))
    return Form(t, jp.get("title", ""), fields)


def _role_from_label(label: str, kind: str) -> str:
    low = label.lower()
    if kind == "tel" or re.fullmatch(r"(mobile )?phone( number)?", low):
        return "phone"
    if kind == "file" and "cover" in low:
        return "cover_letter"
    if kind == "file" and re.search(r"\b(resume|cv)\b", low):
        return "resume"
    if kind in ("text",) and "linkedin" in low and len(low) < 60:
        return "linkedin"
    if kind in ("text",) and "github" in low and len(low) < 60:
        return "github"
    if kind in ("text",) and re.search(r"\b(portfolio|personal website|website)\b", low) and len(low) < 60:
        return "website"
    return ""


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))).strip()
