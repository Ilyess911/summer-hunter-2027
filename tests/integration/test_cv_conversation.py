"""AI CV Builder lot 5: edits by message, buttons, settings, upload, conversation routing."""

from __future__ import annotations

import time

import pytest

from summer_hunter.cv_builder import edits, prefs
from summer_hunter.cv_builder.intent import CVRequest, analyze, context_from
from summer_hunter.cv_builder.profile import INFERRED, ProfileStore
from summer_hunter.cv_builder.upload import items, novel_lines
from summer_hunter.cv_builder.versions import VersionStore
from summer_hunter.database.repository import Repository
from tests.integration.test_cv_document import needs_tex
from tests.integration.test_cv_profile import _bot, _press, _say


@pytest.mark.parametrize("text, expected", [
    ("Mets davantage en avant mon projet de robotique médicale.", {"emphasis": ["projet de robotique médicale"]}),
    ("Fais-le en anglais et sur une seule page.", {"language": "en", "pages": 1}),
    ("retire mon projet Medical IA et retire Amundi", {"exclude": ["projet Medical IA", "Amundi"]}),
    ("insiste sur la data, 2 pages", {"pages": 2, "emphasis": ["data"]}),
    ("Pour un poste de Sales Engineer", {"target_role": "Sales Engineer"}),
    ("plus de Nexya et en français", {"language": "fr", "emphasis": ["Nexya"]}),
    ("version générique", {"personalization": "light"}),
    ("ok merci", {}),
])
def test_edit_parser(text, expected):
    assert edits.parse(text).overrides == expected


def test_a_personal_statement_is_a_fact_not_an_edit():
    e = edits.parse("J'ai obtenu une certification IBM")
    assert e.facts and not e.overrides


def test_settings_cycle_and_apply(sessions, settings):
    with sessions() as s:
        repo = Repository(s)
        assert prefs.load(repo) == prefs.DEFAULTS
        prefs.cycle(repo, "language")          # auto -> en
        prefs.cycle(repo, "language")          # en -> fr
        prefs.cycle(repo, "pages")             # 1 -> 2
        current = prefs.load(repo)
        assert current["language"] == "fr" and current["pages"] == "2"
        ctx = context_from(settings, s)
        req = prefs.apply(analyze("CV Tech Sales à Dublin", ctx), current, "CV Tech Sales à Dublin")
        assert (req.language, req.pages) == ("fr", 2)
        explicit = prefs.apply(analyze("CV Tech Sales en anglais, 1 page", ctx), current, "CV Tech Sales en anglais, 1 page")
        assert (explicit.language, explicit.pages) == ("en", 1)          # the request wins
        repo.set_kv(prefs.KEY, "{not json")
        assert prefs.load(repo) == prefs.DEFAULTS


def test_upload_proposes_only_new_lines(settings):
    from summer_hunter.web.cv import load_content
    from summer_hunter.bot.cv_handlers import _all_strings

    known = _all_strings(load_content(settings.config_dir))
    bullet = known[[i for i, k in enumerate(known) if k.startswith("Automate the consolidation")][0]]
    text = (f"PROFESSIONAL EXPERIENCE\n• {bullet[:60]}\n  {bullet[60:]}\n"
            "• Led a team of 4 students to automate a pick-and-place cell with an ABB robot.\n"
            "ilyess@example.com | +33 6 00 00 00 00\n")
    assert any(i.startswith("Automate the consolidation") and i.endswith("decisions.") for i in items(text))
    assert novel_lines(text, known) == ["Led a team of 4 students to automate a pick-and-place cell with an ABB robot."]


@needs_tex
def test_exclude_and_light_personalization(settings, sessions, tmp_path):
    from summer_hunter.cv_builder.document import build

    with sessions() as s:
        ctx = context_from(settings, s)
    req = analyze("CV pour un stage Product Management chez Databricks", ctx)
    base = build(settings.config_dir, tmp_path / "a", req, [])
    assert any("Medical" in p["name"] for p in base.cv["projects"]) and "Databricks" in base.cv["summary"]
    edited = CVRequest.from_dict(req.to_dict() | {"exclude": ["Medical IA", "Amundi"], "personalization": "light"})
    res = build(settings.config_dir, tmp_path / "b", edited, [])
    assert not any("Medical" in p["name"] for p in res.cv["projects"])
    assert not any("Amundi" in e["org"] for e in res.cv["experiences"])
    assert "Databricks" not in res.cv["summary"] and res.ok


@needs_tex
def test_full_conversation(settings, sessions):
    bot, fake = _bot(settings, sessions)
    bot.cvb.buffer_wait_s = 0.05
    _say(bot, "/cv")
    assert "Envoie-moi une offre" in fake.sent[-1]["text"]
    _say(bot, "Je veux un CV pour un stage en AI Business Development à Singapour.")
    deadline = time.time() + 30
    while time.time() < deadline and not any("APPLICATION READY" in m["text"] for m in fake.sent):
        time.sleep(0.1)
    with bot.cvb._lock:
        pass
    texts = "\n".join(m["text"] for m in fake.sent)
    assert "Analyse en cours" in texts and "CV-0001 v1" in texts and "Singapore" in texts
    report = fake.sent[-1]
    assert "Rapport d'optimisation" in report["text"]
    datas = [b["callback_data"] for row in report["reply_markup"]["inline_keyboard"] for b in row]
    assert datas == ["cvr:1", "cve:1", "cvs:1", "cvl:1", "cvp:1", "cvd:1", "cvk:1", "cvm:1"]

    bot.cvb.edit(1, "Fais-le en français et sur 2 pages", background=False)
    assert "CV-0001 v2" in "\n".join(m["text"] for m in fake.sent)
    with sessions() as s:
        v2 = VersionStore(s, settings.data_dir, settings.config_dir).get(2)
        assert (v2.language, v2.parent_id, v2.request["pages"]) == ("fr", 1, 2)

    # a short message right after a CV edits it (here: synchronously through the parser + derive)
    e = edits.parse("retire Amundi")
    bot.cvb.edit(bot.cvb._active(), "retire Amundi", e, background=False)
    with sessions() as s:
        v3 = VersionStore(s, settings.data_dir, settings.config_dir).get(3)
        assert v3.parent_id == 2 and "Amundi" in v3.request["exclude"]

    # a statement becomes a fact to confirm, never a CV change
    n = len(fake.sent)
    bot.cvb.edit(3, "J'ai obtenu une certification IBM", background=False)
    assert any("Souhaites-tu ajouter" in m["text"] for m in fake.sent[n:])
    with sessions() as s:
        assert any("IBM" in f.text and f.status == INFERRED for f in ProfileStore(s).facts())
        assert VersionStore(s, settings.data_dir, settings.config_dir).latest().id == 3

    # buttons
    _press(bot, "cvk:3")
    with sessions() as s:
        assert VersionStore(s, settings.data_dir, settings.config_dir).get(3).saved
    docs = len(fake.docs)
    _press(bot, "cvp:3")
    _press(bot, "cvd:3")
    assert len(fake.docs) == docs + 2
    _press(bot, "cvs:3")
    assert "Styles disponibles" in fake.sent[-1]["text"]
    _press(bot, "cve:3")
    assert "Écris la modification" in fake.sent[-1]["text"] and bot.cvb._await[0] == "edit"
    bot.cvb._regenerate(3, background=False)
    with sessions() as s:
        assert VersionStore(s, settings.data_dir, settings.config_dir).latest().version == 4


def test_unrelated_text_is_left_to_the_bot(settings, sessions):
    bot, _ = _bot(settings, sessions)
    assert bot.cvb.handle_text("bonjour") is False              # no CV yet, nothing awaited
    with sessions() as s:
        Repository(s).set_kv("cv_active", f"7:{time.time() - 3 * 3600:.0f}")   # expired
        s.commit()
    assert bot.cvb.handle_text("en anglais") is False
    assert bot.cvb.handle_text("https://jobs.example.com/1") is False


def test_settings_command_and_buttons(settings, sessions):
    bot, fake = _bot(settings, sessions)
    _say(bot, "/cv_settings")
    assert "Réglages CV" in fake.sent[-1]["text"] and "ATS Classic" in fake.sent[-1]["text"]
    _press(bot, "cvo:0:language")
    assert "anglais" in fake.sent[-1]["text"]
    _press(bot, "cvo:0:nope")


def test_upload_flow(settings, sessions, monkeypatch, tmp_path):
    bot, fake = _bot(settings, sessions)
    from docx import Document

    src = tmp_path / "old.docx"
    d = Document()
    d.add_paragraph("Led a team of 4 students to automate a pick-and-place cell with an ABB robot.")
    d.save(src)

    def fake_download(file_id, dest, max_bytes=10_000_000):  # noqa: ANN001, ANN202
        dest.write_bytes(src.read_bytes())
        return dest
    monkeypatch.setattr(bot.telegram, "download_file", fake_download)
    _say(bot, "/cv_upload")
    assert "Envoie ton CV" in fake.sent[-1]["text"]
    bot.handle_update({"update_id": 3, "message": {"chat": {"id": int(fake_chat())}, "document": {
        "file_id": "f1", "file_name": "CV.docx", "file_size": 2000}}})
    texts = "\n".join(m["text"] for m in fake.sent)
    assert "1 ligne(s) absentes" in texts and "ABB robot" in texts
    assert not list((settings.data_dir / "cv_builder" / "uploads").glob("*"))      # temporary copy deleted
    doc_update = {"update_id": 4, "message": {"chat": {"id": int(fake_chat())}, "document": {
        "file_id": "f1", "file_name": "CV.docx", "file_size": 2000}}}
    bot.handle_update(doc_update)               # no /cv_upload: a PDF/DOCX may be a job description
    assert "lance d'abord /cv_upload" in fake.sent[-1]["text"]
    _say(bot, "/cv_upload")
    bot.handle_update(doc_update)
    assert "rien de nouveau" in fake.sent[-1]["text"]


def test_upload_skips_employer_demand_sections():
    text = ("EXPERIENCE\n• Led a team of 4 students to automate a pick-and-place cell with an ABB robot.\n"
            "Requirements\n• Strong Python skills and 3 years of experience with ROS required.\n"
            "SKILLS\n• Coordinated weekly maintenance planning meetings with the line managers.\n")
    found = items(text)
    assert not any("ROS required" in i for i in found)
    assert any("ABB robot" in i for i in found) and any("maintenance planning" in i for i in found)


def test_command_clears_a_pending_question(settings, sessions):
    bot, _ = _bot(settings, sessions)
    _say(bot, "/cv")
    assert bot.cvb._await[0] == "request"
    _say(bot, "/top10")
    assert bot.cvb._await is None
    assert bot.cvb.handle_text("https://jobs.example.com/1") is False      # the link goes to offer ingestion


def test_two_quick_cv_commands_stay_two_requests(settings, sessions, monkeypatch):
    bot, _ = _bot(settings, sessions)
    built = []
    monkeypatch.setattr(bot.cvb, "_generate", lambda text: built.append(text))
    bot.cvb.buffer_wait_s = 0.05
    bot.cvb.generate("CV Tech Sales")
    bot.cvb.generate("CV Robotics Engineer")
    time.sleep(0.3)
    assert sorted(built) == ["CV Robotics Engineer", "CV Tech Sales"]


def test_safe_error_never_carries_the_message():
    from summer_hunter.bot.cv_handlers import safe_error

    try:
        raise ValueError("Automate the consolidation of aircraft equipment data")
    except ValueError as exc:
        info = safe_error(exc)
    assert info["error"] == "ValueError" and "Automate" not in str(info)


def fake_chat() -> str:
    from tests.integration.test_crm import CHAT

    return CHAT
