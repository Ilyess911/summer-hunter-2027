"""Workday (phase 2): detection, answers per field, memory scope, password, and the send guards (no browser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from summer_hunter.apply import answers as A
from summer_hunter.apply import ats, service, workday
from summer_hunter.utils.settings import load_yaml

CFG = load_yaml(Path("config/apply.yaml"))
HEADER = {"name": "Ilyess Assadi", "email": "me@example.com", "phone": "+33 6 12 34 56 78", "links": []}


def test_detect_workday() -> None:
    t = ats.detect("https://salesforce.wd12.myworkdayjobs.com/Futureforce_Internships/job/France---Paris/Intern_JR1-2")
    assert t.kind == "workday" and t.board == "salesforce.wd12.myworkdayjobs.com"
    assert t.form_url.endswith("/en-US/Futureforce_Internships/job/France---Paris/Intern_JR1-2/apply/autofillWithResume")
    t2 = ats.detect("https://airbus.wd3.myworkdayjobs.com/fr-FR/Airbus/job/Toulouse/Stage_JR9/apply?source=li")
    assert t2.job_id == "Toulouse/Stage_JR9"


def test_greenhouse_on_company_site() -> None:
    assert ats.gh_jid("https://stripe.com/jobs/search?gh_jid=8194291") == "8194291"
    t = ats.detect("https://stripe.com/jobs/search?gh_jid=8194291", gh_board="stripe")
    assert t.kind == "greenhouse" and t.form_url == "https://job-boards.greenhouse.io/stripe/jobs/8194291"
    assert ats.detect("https://stripe.com/jobs/search?gh_jid=8194291") is None     # board unknown: no guess


def _f(label: str, kind: str = "text", options: list[str] | None = None, fid: str = "", req: bool = True) -> workday.WDField:
    return workday.WDField("f0", fid, label, req, kind, "", options or [])


def test_resolve_fields() -> None:
    who = A.Applicant(HEADER, CFG)
    r = lambda f, known=None: workday.resolve(f, known or {}, who, CFG, ["FR"], "Acme")  # noqa: E731
    assert r(_f("First Name", fid="legalNameSection_firstName")).value == "Ilyess"
    assert r(_f("Phone Number", fid="phone-number")).value == "612345678"
    assert r(_f("Phone Device Type", "select", ["Mobile", "Home"], fid="phone-device-type")).value == "Mobile"
    assert r(_f("Address Line 1", fid="addressSection_addressLine1")).missing          # unknown: asked
    assert r(_f("Address Line 1", fid="addressSection_addressLine1"), {"address line 1": "1 rue X"}).value == "1 rue X"
    assert r(_f("Middle Name", fid="legalNameSection_middleName")).missing
    assert r(_f("Will you require visa sponsorship to work in France?", "select", ["Yes", "No"])).value == "No"
    assert r(_f("What are your salary expectations?")).missing
    assert r(_f("I acknowledge and agree to Acme's Candidate Privacy Notice", "checkbox")).value is True
    assert r(_f("I agree to the terms and confirm all information is accurate", "checkbox")).missing


def test_memory_never_keeps_company_specific_answers(tmp_path) -> None:  # noqa: ANN001
    keep = A.Answer("postal code", "Postal Code", "text", True, [], "", "75015", A.USER)
    no = [A.Answer("are you willing to relocate", "Are you willing to relocate?", "select", True, [], "", "Yes", A.USER),
          A.Answer("k1", "Have you previously worked for Acme?", "select", True, [], "", "No", A.USER),
          A.Answer("k2", "Expected salary", "text", True, [], "", "2000", A.USER),
          A.Answer("k3", "Do you require sponsorship?", "select", True, [], "", "No", A.USER)]
    for a in [keep, *no]:
        service.remember(tmp_path, a)
    assert service.memory(tmp_path) == {"postal code": "75015"}


def test_password_has_every_class() -> None:
    pw = workday.new_password()
    assert len(pw) == 16 and any(c.isupper() for c in pw) and any(c.islower() for c in pw)
    assert any(c.isdigit() for c in pw) and any(c in "!@#$%&*?" for c in pw)


def _wd_state(status: str, rev: int = 3) -> dict:
    t = ats.detect("https://acme.wd1.myworkdayjobs.com/Ext/job/Paris/Intern_JR1")
    return {"opp_id": 9, "kind": "workday", "status": status, "target": t.__dict__, "known": {}, "hashes": {},
            "resume": None, "title": "Intern", "company": "Acme", "rev": rev, "questions": [], "filled": []}


class Spy:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, chat, text, buttons=None, **_):  # noqa: ANN001
        self.sent.append(text)
        return len(self.sent)


@pytest.mark.parametrize("status", ["needs", "verify_email", "invalid"])
def test_workday_submit_only_from_review(settings, tmp_path, monkeypatch, status) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    service.save_state(tmp_path, 9, _wd_state(status))
    monkeypatch.setattr(workday, "run", lambda *a, **k: pytest.fail("must not open the browser"))
    spy = Spy()
    service.submit(settings, None, spy, 9, 3)
    assert "refusé" in spy.sent[-1] and service.load_state(tmp_path, 9)["status"] == status


def test_workday_submit_clicked_unknown_is_final(settings, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    service.save_state(tmp_path, 9, _wd_state("ready"))
    calls = []

    def fake_run(*a, **k):  # noqa: ANN002, ANN003, ANN202
        calls.append(k.get("submit"))
        assert service.load_state(tmp_path, 9)["status"] == "submitting"
        return workday.WDResult("error", None, "Review", clicked=True, detail="crash")
    monkeypatch.setattr(workday, "run", fake_run)
    monkeypatch.setattr(service, "load_content", lambda *_: {"header": HEADER}, raising=False)
    spy = Spy()
    spy.send_photo = lambda *a, **k: 1
    monkeypatch.setattr(service, "_mark_applied", lambda *a: pytest.fail("not applied"))
    monkeypatch.setattr(service.Repository, "set_kv", lambda *a: None)

    class _S:
        def __enter__(self): return self  # noqa: E704
        def __exit__(self, *a): return False  # noqa: E704
        def commit(self): pass  # noqa: E704
    service.submit(settings, lambda: _S(), spy, 9, 3)
    assert calls == [True] and service.load_state(tmp_path, 9)["status"] == "unconfirmed"
    service.submit(settings, lambda: _S(), spy, 9, 4)
    assert calls == [True]                                              # never a second click


def test_render_workday_states() -> None:
    text, buttons = service.render_workday(_wd_state("verify_email"))
    assert "mail" in text and buttons[0][0]["callback_data"] == "aa:9:cont"
    text, buttons = service.render_workday(_wd_state("ready"))
    assert buttons[0][0]["callback_data"] == "aa:9:go:3"
    st = _wd_state("needs")
    st["questions"] = [A.Answer("postal code", "Postal Code", "text", True, [], "").to_dict()]
    text, buttons = service.render_workday(st)
    assert "1. Postal Code" in text and all("go" not in b.get("callback_data", "") for r in buttons for b in r)
    text, buttons = service.render_workday(_wd_state("unconfirmed"))
    assert buttons[0][0]["callback_data"] == "aa:9:done"


def test_roles_never_take_someone_elses_field() -> None:
    who = A.Applicant(HEADER, CFG)
    for label in ("Referrer's email", "Country of birth", "Employer name", "Emergency contact phone number"):
        a = workday.resolve(_f(label), {}, who, CFG, ["FR"], "Acme")
        assert a.missing, label
    assert workday.resolve(_f("Phone Number", fid="emergencyContact_phone-number"), {}, who, CFG, ["FR"], "Acme").missing
    assert workday.resolve(_f("I certify that I understand the travel requirements", "checkbox"), {}, who, CFG,
                           ["FR"], "Acme").missing


def test_workday_changed_review_is_not_submitted(settings, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    st = _wd_state("ready")
    st["review_fp"] = "old"
    service.save_state(tmp_path, 9, st)
    seen = {}

    def fake_run(*a, **k):  # noqa: ANN002, ANN003, ANN202
        seen["fp"] = k.get("approved_fp")
        r = workday.WDResult("review", None, "changed")
        r.detail = "new"
        return r
    monkeypatch.setattr(workday, "run", fake_run)
    monkeypatch.setattr(service, "_mark_applied", lambda *a: pytest.fail("not applied"))

    class _S:
        def __enter__(self): return self  # noqa: E704
        def __exit__(self, *a): return False  # noqa: E704
        def commit(self): pass  # noqa: E704
    monkeypatch.setattr(service.Repository, "set_kv", lambda *a: None)
    spy = Spy()
    service.submit(settings, lambda: _S(), spy, 9, 3)
    after = service.load_state(tmp_path, 9)
    assert seen["fp"] == "old" and after["status"] == "ready" and after["review_fp"] == "new" and after["rev"] == 4
    assert any("RIEN envoyé" in m for m in spy.sent)


def test_pasted_offers_find_and_take_the_form_link(settings, tmp_path, monkeypatch, sessions) -> None:  # noqa: ANN001
    from summer_hunter.database.models import Opportunity

    assert ats.best_url("Apply: https://example.com/x and https://jobs.lever.co/a/9053e477-835b-40ac-9fd9-52411b63edd4.") \
        == "https://jobs.lever.co/a/9053e477-835b-40ac-9fd9-52411b63edd4"
    assert ats.best_url("https://career4.successfactors.com/career?company=x") is None
    with sessions() as s:
        opp = Opportunity(canonical_key="p1", company_name="Acme", normalized_company="acme", title="Intern",
                          normalized_title="intern", official_url="pasted:abc", content_hash="h")
        s.add(opp)
        s.commit()
        oid = opp.id
    called = []
    monkeypatch.setattr(service, "prepare", lambda st, se, tg, i, explicit=True: called.append((i, explicit)))
    spy = Spy()
    service.attach_link(settings, sessions, spy, oid, "voilà https://career4.successfactors.com/x")
    assert not called and "non géré" in spy.sent[-1]
    service.attach_link(settings, sessions, spy, oid, "https://acme.wd1.myworkdayjobs.com/Ext/job/Paris/Intern_JR1")
    assert called == [(oid, False)]                       # Workday still asks before creating the account
    with sessions() as s:
        assert s.get(Opportunity, oid).apply_url.endswith("Intern_JR1")


def test_plus_alias_and_single_profile_tenants(tmp_path) -> None:  # noqa: ANN001
    assert workday.plus_alias("me.pro@gmail.com", "nvidia.wd5.myworkdayjobs.com") == "me.pro+nvidia@gmail.com"
    assert workday.plus_alias("me+x@gmail.com", "acme.wd1.myworkdayjobs.com") == "me+acme@gmail.com"
    assert workday.plus_alias("me@outlook.com", "acme.wd1.myworkdayjobs.com") is None
    assert not service._tenant_flag(tmp_path, "nvidia.wd5.myworkdayjobs.com")
    service._tenant_flag(tmp_path, "nvidia.wd5.myworkdayjobs.com", "single_profile")
    assert service._tenant_flag(tmp_path, "nvidia.wd5.myworkdayjobs.com")
    st = _wd_state("auth_failed")
    st["detail"] = "single_profile"
    text, _ = service.render_workday(st)
    assert "plusieurs comptes" in text


def test_given_names_and_device_type_from_profile() -> None:
    who = A.Applicant(HEADER, CFG)
    r = lambda f: workday.resolve(f, {}, who, CFG, ["FR"], "Acme")  # noqa: E731
    assert r(_f("Given Name(s)")).value == "Ilyess" and r(_f("Family Name(s)")).value == "Assadi"
    dev = r(_f("Phone Device Type", "select", ["Home", "Home Cellular", "Mobile", "Work"], fid="phone-device-type"))
    assert dev.value == "Mobile" and dev.source == A.PROFILE
    cell = r(_f("Phone Device Type", "select", ["Home", "Home Cellular"], fid="phone-device-type"))
    assert cell.value == "Home Cellular"
    src = r(_f("How Did You Hear About Us?", "multiselect"))
    assert src.extra["alts"][0] == "Company Website" and not src.missing


def test_work_history_from_cv_content():
    from summer_hunter.apply.service import work_history

    cv = {"experiences": [
        {"org": "Air France Industries -- KLM E&M", "dates": "Sep 2024 -- Present", "role": "Apprentice -- Equipment",
         "location": "Paris, France", "bullets": [{"text": "a"}, {"text": "b"}, {"text": "c"}, {"text": "d"}]},
        {"org": "Amundi", "dates": "Jul 2023 -- Aug 2023", "role": "Assistant", "bullets": ["x"]},
        {"org": "No dates", "role": "Ghost"}]}
    jobs = work_history(cv)
    assert [j["company"] for j in jobs] == ["Air France Industries", "Amundi"]
    assert jobs[0] == {**jobs[0], "title": "Apprentice - Equipment", "start": "09/2024", "end": None, "current": True}
    assert jobs[0]["description"].count("\n") == 2           # three bullets at most
    assert jobs[1]["start"] == "07/2023" and jobs[1]["end"] == "08/2023" and not jobs[1]["current"]


def test_degree_and_years_come_from_profile_not_model():
    from summer_hunter.apply import workday as wd

    cfg = {"applicant": {"degree": "Master's Degree", "school_start_year": 2024, "graduation_year": 2027}}
    who = _who() if "_who" in globals() else None
    f = wd.WDField(idx="1", id="degree", label="Degree", required=True, kind="select", value="",
                   options=["Bachelors", "Masters", "PhD"])
    a = wd.resolve(f, {}, who, cfg, [], "NVIDIA")
    assert a.value == "Masters"
    y = wd.WDField(idx="2", id="lastYearAttended", label="To (Actual or Expected)", required=True, kind="date",
                   value="", options=[])
    assert wd.resolve(y, {}, who, cfg, [], "NVIDIA").value == "2027"
