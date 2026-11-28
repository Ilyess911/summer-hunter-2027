"""Auto-apply phase 1: platform detection, form reading, answers, and the send guards (no browser, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from summer_hunter.apply import answers as A
from summer_hunter.apply import ats, service
from summer_hunter.utils.settings import load_yaml

HEADER = {"name": "Ilyess Assadi", "email": "me@example.com", "phone": "+33 6 00 00 00 00",
          "links": [{"url": "https://www.linkedin.com/in/x/"}, {"url": "https://github.com/x"},
                    {"url": "https://x.github.io"}]}


@pytest.fixture()
def cfg(settings):  # noqa: ANN001
    return load_yaml(settings.config_dir / "apply.yaml") if (settings.config_dir / "apply.yaml").exists() else \
        load_yaml(__import__("pathlib").Path("config/apply.yaml"))


def test_detect_platforms() -> None:
    gh = ats.detect("https://job-boards.eu.greenhouse.io/bybit/jobs/4974752101")
    assert (gh.kind, gh.board, gh.job_id, gh.eu) == ("greenhouse", "bybit", "4974752101", True)
    lv = ats.detect("https://jobs.lever.co/lalamove/9053e477-835b-40ac-9fd9-52411b63edd4")
    assert lv.kind == "lever" and lv.form_url.endswith("/apply")
    ab = ats.detect("https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245")
    assert ab.kind == "ashby" and ab.form_url.endswith("/application")
    assert ats.detect("https://career4.successfactors.com/career?company=x") is None
    assert ats.detect("http://jobs.lever.co/a/9053e477-835b-40ac-9fd9-52411b63edd4") is None   # https only


def _client(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        for prefix, resp in routes.items():
            if str(req.url).startswith(prefix):
                return resp
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_greenhouse_form_from_api() -> None:
    body = {"title": "Intern", "education": "education_optional", "questions": [
        {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text"}]},
        {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "input_file"},
                                                           {"name": "resume_text", "type": "textarea"}]},
        {"label": "LinkedIn Profile", "required": False, "fields": [{"name": "question_1", "type": "input_text"}]},
        {"label": "Visa?", "required": True, "fields": [{"name": "question_2", "type": "multi_value_single_select",
                                                         "values": [{"label": "Yes"}, {"label": "No"}]}]}],
        "location_questions": [{"label": "Location", "required": True, "fields": [{"name": "location"}]}]}
    t = ats.detect("https://job-boards.greenhouse.io/acme/jobs/1")
    form = ats.fetch_form(t, _client({"https://boards-api.greenhouse.io/": httpx.Response(200, json=body)}))
    roles = {f.key: f.role for f in form.fields}
    assert roles["first_name"] == "first_name" and roles["resume"] == "resume" and roles["question_1"] == "linkedin"
    assert next(f for f in form.fields if f.key == "resume").kind == "file"
    assert next(f for f in form.fields if f.key == "question_2").options == ["Yes", "No"]
    assert {"candidate-location", "school--0"} <= set(roles)
    assert not next(f for f in form.fields if f.key == "school--0").required


def test_lever_form_from_html() -> None:
    tpl = {"fields": [{"type": "multiple-choice", "text": "Onsite OK?", "required": True,
                       "options": [{"text": "Yes"}, {"text": "No"}]},
                      {"type": "textarea", "text": "Expected salary?", "required": True}]}
    page = (f'<h2>Intern</h2><input name="name" required><input name="email" required><input name="org">'
            f'<input name="urls[LinkedIn]"><input type="hidden" name="cards[c1][baseTemplate]" '
            f'value="{json.dumps(tpl).replace(chr(34), "&quot;")}">'
            '<label><input type="checkbox" name="consent[store]" required> I consent</label>')
    t = ats.detect("https://jobs.lever.co/acme/9053e477-835b-40ac-9fd9-52411b63edd4")
    form = ats.fetch_form(t, _client({"https://jobs.lever.co/": httpx.Response(200, text=page)}))
    by = {f.key: f for f in form.fields}
    assert by["name"].role == "full_name" and by["name"].required and not by["org"].required
    assert by["cards[c1][field0]"].options == ["Yes", "No"] and by["cards[c1][field1]"].kind == "textarea"
    assert by["consent[store]"].role == "consent"


def _form(*fields: ats.Field) -> ats.Form:
    return ats.Form(ats.Target("greenhouse", "a", "1", "https://job-boards.greenhouse.io/a/jobs/1"), "Intern", list(fields))


def test_answers_profile_rules_and_asks(cfg) -> None:  # noqa: ANN001
    F = ats.Field
    form = _form(
        F("first_name", "First Name", "text", True, role="first_name"),
        F("resume", "Resume", "file", True, role="resume"),
        F("cover_letter", "Cover", "file", False, role="cover_letter"),
        F("q1", "What are your salary expectations?", "text", True),
        F("q2", "Gender", "select", True, ["Male", "Female", "Prefer not to say"]),
        F("q3", "Are you legally authorized to work in Thailand?", "select", True, ["Yes", "No"]),
        F("q4", "Do you require visa sponsorship to work in France?", "select", True, ["Yes", "No"]),
        F("q5", "Are you available from 24 May 2027 to 30 July 2027?", "select", True, ["Yes", "No"]),
        F("q6", "Are you presently employed by any company within Booking Holdings?", "select", True, ["Yes", "No"]),
        F("q7", "Have you ever worked for Air France or its subsidiaries?", "select", True, ["Yes", "No"]),
        F("q8", "Tell us about a project", "textarea", True))
    out = {a.key: a for a in A.build(form, HEADER, cfg, ["FR", "MA"], {"resume": "/tmp/cv.pdf", "cover_letter": None})}
    assert out["first_name"].value == "Ilyess" and out["first_name"].source == A.PROFILE
    assert out["resume"].value == "/tmp/cv.pdf" and out["cover_letter"].missing
    assert out["q1"].value == "In line with your internship pay policy"   # salary: standing answer (28 Nov)
    assert out["q2"].value == "Prefer not to say"
    assert out["q3"].value == "No" and "hors UE" in out["q3"].note   # outside the EU: no visa yet (28 Nov rule)
    assert out["q4"].value == "No"                                    # French citizen, no sponsorship in France
    assert out["q5"].value == "Yes"                                   # inside April-August 2027
    assert out["q6"].value == "No"
    assert out["q7"].value == "Yes" and "Air France" in out["q7"].note   # own employer named: from the CV
    assert out["q8"].missing and out["q8"].source == A.ASK
    blocking = {a.key for a in A.blocking(list(out.values()))}
    assert blocking == {"q8"}


class FakeLLM:
    def __init__(self, data: dict) -> None:
        self.data, self.calls = data, 0

    def complete_json(self, system, user, schema, max_tokens):  # noqa: ANN001, ANN201
        self.calls += 1
        assert "salary" not in user.lower()          # never-guessed questions are not sent to the model
        return type("R", (), {"data": self.data})()


def test_model_answers_are_validated(cfg) -> None:  # noqa: ANN001
    F = ats.Field
    form = _form(F("q1", "How many hours per week?", "text", True), F("q2", "Years of Python?", "select", True, ["0-1", "2-3"]),
                 F("q3", "Why us?", "textarea", True), F("q4", "Puzzle", "text", True))
    answers = A.build(form, HEADER, cfg, ["FR"], {})
    # numbering covers only what the model may answer: q2, q3, q4 (hours per week are always asked)
    llm = FakeLLM({"answers": [{"n": 1, "answer": "5+"}, {"n": 2, "answer": "UNKNOWN"}, {"n": 9, "answer": "x"}]})
    assert A.fill_with_model(llm, answers, "facts", "Intern", "Acme", "posting") == 0
    assert all(a.missing for a in answers)                # an option outside the list and UNKNOWN are both refused
    llm = FakeLLM({"answers": [{"n": 1, "answer": "0-1"}, {"n": 2, "answer": "I built X."}]})
    assert A.fill_with_model(llm, answers, "facts", "Intern", "Acme", "posting") == 2
    assert answers[1].value == "0-1" and answers[1].source == A.MODEL


def test_set_value_parsing() -> None:
    a = A.Answer("k", "Q", "select", True, ["Yes", "No"], "")
    assert A.set_value(a, "oui") and a.value == "Yes" and a.source == A.USER
    assert not A.set_value(a, "maybe")
    b = A.Answer("k", "Q", "boolean", True, [], "")
    assert A.set_value(b, "non") and b.value is False
    m = A.Answer("k", "Q", "multiselect", True, ["Native", "Fluent", "Basic"], "")
    assert A.set_value(m, "native, basic") and m.value == ["Native", "Basic"]


def _state(tmp_path, status: str, missing: bool = False) -> dict:  # noqa: ANN001
    a = A.Answer("q", "Question", "text", True, [], "", None if missing else "ok", A.USER)
    form = _form(ats.Field("q", "Question", "text", True))
    return {"opp_id": 7, "status": status, "form": form.to_dict(), "answers": [a.to_dict()],
            "title": "Intern", "company": "Acme", "rev": 1}


def test_render_blocks_send_until_complete(tmp_path) -> None:  # noqa: ANN001
    text, buttons = service.render(_state(tmp_path, "ready", missing=True))
    assert "à toi" in text and all(b["callback_data"] != "aa:7:go:1" for r in buttons for b in r)
    _, buttons = service.render(_state(tmp_path, "ready"))
    assert buttons[0][0]["callback_data"] == "aa:7:go:1"


class Spy:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_message(self, chat, text, buttons=None, **_):  # noqa: ANN001
        self.sent.append(text)
        return len(self.sent)


@pytest.mark.parametrize("status", ["submitting", "submitted", "unconfirmed", "cancelled"])
def test_submit_never_twice(settings, tmp_path, monkeypatch, status) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    service.save_state(tmp_path, 7, _state(tmp_path, status))
    clicked = []
    monkeypatch.setattr(service.browser, "fill", lambda *a, **k: clicked.append(1))
    spy = Spy()
    service.submit(settings, None, spy, 7, 1)
    assert not clicked and "refusé" in spy.sent[-1]
    assert service.load_state(tmp_path, 7)["status"] == status


def test_submit_marks_submitting_before_click(settings, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    service.save_state(tmp_path, 7, _state(tmp_path, "ready"))
    seen = []

    def fake_fill(url, kind, answers, shot, submit=False, **_):  # noqa: ANN001, ANN202
        seen.append((service.load_state(tmp_path, 7)["status"], submit))
        raise RuntimeError("browser died mid-click")
    monkeypatch.setattr(service.browser, "fill", fake_fill)
    spy = Spy()
    service.submit(settings, None, spy, 7, 1)
    assert seen == [("submitting", True)]
    assert service.load_state(tmp_path, 7)["status"] == "unconfirmed"     # unknown outcome: never resent blindly
    service.submit(settings, None, spy, 7, 1)
    assert len(seen) == 1


def test_reply_updates_answers(settings, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    service.save_state(tmp_path, 7, _state(tmp_path, "ready", missing=True))
    previews = []
    monkeypatch.setattr(service, "_preview", lambda *a, **k: previews.append(1))
    spy = Spy()
    service.on_reply(settings, None, spy, 7, "1: Paris\n9: nope", 1)
    st = service.load_state(tmp_path, 7)
    assert st["answers"][0]["value"] == "Paris" and previews == [1]
    assert any("numéro inconnu" in s for s in spy.sent)


# Codex review fixes (22 Nov 2026) ---------------------------------------------------------------

def test_visa_polarity_and_ambiguity() -> None:
    w = A._work_auth
    assert w("Are you legally authorized to work in France without visa sponsorship?", ["Yes", "No"], ["FR"])[0] == "Yes"
    assert w("Will you now or in the future require visa sponsorship to work in Germany?", ["Yes", "No"], ["FR"])[0] == "No"
    assert w("Are you authorized to work in Spain or will you need sponsorship?", ["Yes", "No"], ["FR"])[0] is None
    assert w("Do you have the right to work in Singapore?", ["Yes", "No"], ["FR"])[0] == "No"
    assert w("Will you require sponsorship to work in Japan?", ["Yes", "No"], ["FR"])[0] == "Yes"
    # the country comes from the job when the label does not name it
    q = "Are you legally authorized to work in the country where this position is located?"
    assert w(q, ["Yes", "No"], ["FR"], "US")[0] == "No" and w(q, ["Yes", "No"], ["FR"], "DE")[0] == "Yes"
    assert w(q, ["Yes", "No"], ["FR"], "")[0] is None
    assert w(q, ["Yes", "No"], ["FR"], "US", "ask")[0] is None


def test_availability_checks_the_end_of_the_stay() -> None:
    f = A._availability
    assert f("Are you available from May 2027 for six months?", ["Yes", "No"], "select")[0] is None
    assert f("Are you available from May 2027 for 3 months?", ["Yes", "No"], "select")[0] == "Yes"
    assert f("Available 24 May 2027 to 30 July 2027 (5 days per week)?", ["Yes", "No"], "select")[0] == "Yes"


def test_employment_and_duration_rules(cfg) -> None:  # noqa: ANN001
    F = ats.Field
    form = _form(F("a", "Are you presently employed by any company within Booking Holdings?", "select", True, ["Yes", "No"]),
                 F("b", "Are you currently employed?", "select", True, ["Yes", "No"]),
                 F("c", "Have you ever worked for Deloitte?", "select", True, ["Yes", "No"]),
                 F("d", "How long have you used Python?", "text", True),
                 F("e", "How many hours per week can you work?", "text", True))
    out = {a.key: a for a in A.build(form, HEADER, cfg, ["FR"], {})}
    assert out["a"].value == "No"
    assert out["c"].value == "No"                         # not one of the CV's employers
    assert all(out[k].missing for k in "bde")


def test_model_index_zero_and_duplicates_ignored(cfg) -> None:  # noqa: ANN001
    form = _form(ats.Field("q1", "Why us?", "textarea", True), ats.Field("q2", "Why this role?", "textarea", True))
    answers = A.build(form, HEADER, cfg, ["FR"], {})
    llm = FakeLLM({"answers": [{"n": 0, "answer": "wrong target"}, {"n": 1, "answer": "first"},
                               {"n": 1, "answer": "dup"}]})
    assert A.fill_with_model(llm, answers, "facts", "Intern", "Acme", "posting") == 1
    assert answers[0].value == "first" and answers[1].missing


def test_field_refused_by_page_blocks_send(tmp_path) -> None:  # noqa: ANN001
    st = _state(tmp_path, "ready")
    st["failed"] = ["q"]
    _, buttons = service.render(st)
    assert all(b["callback_data"] != "aa:7:go:1" for r in buttons for b in r)


def test_clicked_then_unknown_is_never_retryable(settings, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    service.save_state(tmp_path, 7, _state(tmp_path, "ready"))
    monkeypatch.setattr(service.browser, "fill",
                        lambda *a, **k: service.browser.Outcome("error", None, [], "timeout", clicked=True))
    monkeypatch.setattr(service, "_report", lambda *a, **k: None)
    service.submit(settings, None, Spy(), 7, 1)
    assert service.load_state(tmp_path, 7)["status"] == "unconfirmed"
    assert "Rien" in service.cancel(settings, 7)


def test_documents_changed_after_preview_block_send(settings, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    src = tmp_path / "cv.pdf"
    src.write_bytes(b"v1")
    docs, hashes = service.snapshot(tmp_path, 7, {"resume": str(src), "cover_letter": None})
    st = _state(tmp_path, "ready")
    st["hashes"] = hashes
    service.save_state(tmp_path, 7, st)
    Path(docs["resume"]).write_bytes(b"v2")
    monkeypatch.setattr(service.browser, "fill", lambda *a, **k: pytest.fail("must not open the browser"))
    spy = Spy()
    service.submit(settings, None, spy, 7, 1)
    assert "modifiés" in spy.sent[-1] and service.load_state(tmp_path, 7)["status"] == "ready"


class _Req:
    def __init__(self, url: str, body: str = "", rtype: str = "xhr") -> None:
        self.url, self.post_data, self.resource_type = url, body, rtype


class _Route:
    def __init__(self, req: _Req) -> None:
        self.request, self.result = req, None

    def abort(self) -> None:
        self.result = "abort"

    def continue_(self) -> None:
        self.result = "continue"


def test_preview_guard_blocks_private_values() -> None:
    from summer_hunter.apply import browser

    answers = [A.Answer("e", "Email", "email", True, [], "email", "me@example.com", A.PROFILE),
               A.Answer("p", "Phone", "tel", True, [], "phone", "+33 6 12 34 56 78", A.PROFILE)]
    private, n = browser.private_values(answers), [0]
    guard = browser._guard(private, n)
    cases = {("https://x.io/track?e=me%40example.com", ""): "abort",
             ("https://x.io/api", '{"phone":"0612345678"}'): "abort",
             ("https://x.io/places?q=Paris", ""): "continue"}
    for (url, body), want in cases.items():
        r = _Route(_Req(url, body))
        guard(r)
        assert r.result == want, url
    r = _Route(_Req("https://x.io/b", "", "beacon"))
    guard(r)
    assert r.result == "abort" and n[0] == 3


def test_second_review_fixes(settings, tmp_path, monkeypatch, cfg) -> None:  # noqa: ANN001
    from summer_hunter.apply import browser

    assert not browser.SUCCESS.search("Your application is incomplete")
    assert browser.SUCCESS.search("Thank you for applying!")
    assert A._availability("Are you available from April 2025 to August 2027?", ["Yes", "No"], "select") is None
    box = A.Answer("c", "Consent", "checkbox", True, [], "consent", False)
    assert box in A.blocking([box])
    form = _form(ats.Field("a", "Are you currently employed by a company?", "select", True, ["Yes", "No"]))
    assert A.build(form, HEADER, cfg, ["FR"], {})[0].missing
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(service.browser, "fill", lambda *a, **k: pytest.fail("must not open the browser"))
    st = _state(tmp_path, "ready")
    service.save_state(tmp_path, 7, st)
    spy = Spy()
    service.submit(settings, None, spy, 7, 0)                     # stale preview button
    assert "ancien aperçu" in spy.sent[-1]
    st["answers"].append(A.Answer("f", "Resume", "file", True, [], "resume", str(tmp_path / "cv.pdf"), A.FILE).to_dict())
    (tmp_path / "cv.pdf").write_bytes(b"x")
    service.save_state(tmp_path, 7, st)                           # file not in the snapshot manifest
    service.submit(settings, None, spy, 7, 1)
    assert "modifiés" in spy.sent[-1]


def test_reply_to_an_old_message_is_refused(settings, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    service.save_state(tmp_path, 7, _state(tmp_path, "ready", missing=True))
    monkeypatch.setattr(service, "_preview", lambda *a, **k: pytest.fail("no preview"))
    spy = Spy()
    service.on_reply(settings, None, spy, 7, "1: Paris", 0)
    assert "plus le dernier" in spy.sent[-1] and service.load_state(tmp_path, 7)["answers"][0]["value"] is None


def test_security_code_handoff(settings, tmp_path, monkeypatch, sessions) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert service.take_code(settings, sessions, "18300590") is None          # nobody waits: normal message
    spy = Spy()
    src = service.CodeSource(settings, sessions, spy, 7, first="11112222")
    assert src.first() == "11112222" and src.first() is None
    src.ask(0)
    assert "Envoie-le ici" in spy.sent[-1]
    assert service.take_code(settings, sessions, "bonjour tout le monde") is None
    assert service.take_code(settings, sessions, " 18300590 ") == 7
    monkeypatch.setattr(service, "CODE_WAIT_S", 3)
    assert src.wait() == "18300590"
    assert service.take_code(settings, sessions, "18300590") is None          # waiting flag cleared
