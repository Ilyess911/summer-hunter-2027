"""AI CV Builder lot 3: composition, PDF + DOCX rendering, quality checks, /cv on Telegram."""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from summer_hunter.cv_builder import document as doc
from summer_hunter.cv_builder.intent import analyze, context_from
from summer_hunter.cv_builder.profile import USER_CONFIRMED, ProfileStore
from summer_hunter.web.cv import _pdflatex, load_content
from tests.integration.test_cv_profile import _bot, _say

needs_tex = pytest.mark.skipif(_pdflatex() is None or shutil.which("pdfinfo") is None, reason="pdflatex / poppler absent")


@pytest.fixture
def ctx(settings, sessions):
    with sessions() as session:
        return context_from(settings, session)


def fact(kind, text):  # noqa: ANN001, ANN201
    return SimpleNamespace(kind=kind, text=text)


def test_compose_uses_only_verified_text_and_confirmed_facts(settings, ctx):
    content = load_content(settings.config_dir)
    req = analyze("CV pour un stage Robotics Engineer à Singapour", ctx)
    confirmed = [fact("skill", "MATLAB / Simulink"), fact("certification", "IBM AI Engineering"),
                 fact("project", "Cobotized Intra-Oral Dental Scan using AI: academic collaborative robotics project")]
    cv, notes = doc.compose(content, req, confirmed)
    cv.pop("_content")
    skills = [s for items in cv["skills"].values() for s in items]
    assert "MATLAB / Simulink" in skills and "IBM AI Engineering" in cv["skills"]["Certifications"]
    assert list(cv["skills"])[-1] == "Languages"
    assert doc.check_facts(cv, doc.allowed_texts(content, confirmed)).ok
    assert cv["track"] == "industry"


def test_fact_check_catches_an_invented_line(settings, ctx):
    content = load_content(settings.config_dir)
    cv, _ = doc.compose(content, analyze("Tech Sales", ctx), [])
    cv["experiences"][0]["bullets"].append({"text": "Closed 2 M€ of enterprise deals."})
    check = doc.check_facts(cv, doc.allowed_texts(content, []))
    assert not check.ok and "Closed" in check.detail


def test_unconfirmed_facts_never_reach_the_cv(settings, sessions):
    with sessions() as s:
        store = ProfileStore(s)
        store.seed()                       # RoboDK etc. stay INFERRED
        ctx = context_from(settings, s)
        cv, _ = doc.compose(load_content(settings.config_dir), analyze("CV Robotics Engineer", ctx), store.confirmed())
    assert "RoboDK" not in str(cv) and "Hackathon" not in str(cv)


def test_dates_and_sections_checks(settings, ctx):
    cv, _ = doc.compose(load_content(settings.config_dir), analyze("Tech Sales", ctx), [])
    assert doc.check_dates(cv).ok and doc.check_sections(cv).ok
    cv["experiences"][0]["dates"] = "Sep 2026 -- Jan 2025"
    assert not doc.check_dates(cv).ok
    cv["education"] = []
    assert not doc.check_sections(cv).ok


def test_french_request_without_french_content_is_flagged(settings, ctx):
    content, notes = doc.load_content_for(settings.config_dir, "fr")
    assert notes and "jamais traduit" in notes[0]
    cv, _ = doc.compose(content, analyze("CV en français pour un stage maintenance au Maroc", ctx), [])
    assert cv["labels"]["experience"] == "EXPÉRIENCE PROFESSIONNELLE"


def test_emphasis_brings_the_project_first(settings, ctx):
    confirmed = [fact("project", "DevFinOps AI Agent for PLM: AI, automation and PLM environment")]
    req = analyze("CV pour un stage PLM chez Dassault Systèmes, mets en avant DevFinOps", ctx)
    cv, _ = doc.compose(load_content(settings.config_dir), req, confirmed)
    assert cv["projects"][0]["name"] == "DevFinOps AI Agent for PLM"


def test_file_stem(ctx):
    assert doc.file_stem(analyze("Fais-moi un CV pour un stage Robotics Engineer chez une startup à Singapour.", ctx)) == \
        "CV_Robotics_Engineer_Singapore_2027_v1"
    assert doc.file_stem(analyze("CV pour AWS Singapore", ctx), 3).endswith("_2027_v3")


def test_docx_mirrors_the_blocks(settings, ctx, tmp_path):
    cv, _ = doc.compose(load_content(settings.config_dir), analyze("Tech Sales", ctx), [])
    cv.pop("_content")
    path = doc.render_docx(cv, tmp_path / "cv.docx")
    text = doc._norm(doc.docx_text(path))
    for _, block in doc.blocks(cv):
        for piece in block.replace("\t", "\n").split("\n"):
            for part in piece.split("  |  "):
                assert doc._norm(part) in text, part


@needs_tex
def test_build_one_page_pdf_and_docx_identical(settings, ctx, tmp_path):
    req = analyze("Fais-moi un CV pour un stage Robotics Engineer chez une startup à Singapour.", ctx)
    res = doc.build(settings.config_dir, tmp_path, req, [])
    assert res.pdf_path.exists() and res.docx_path.exists() and res.pages == 1
    assert res.ok, [(c.name, c.detail) for c in res.checks if not c.ok]
    assert res.pdf_path.name == "CV_Robotics_Engineer_Singapore_2027_v1.pdf"


@needs_tex
def test_two_pages_allows_more_projects(settings, ctx, tmp_path):
    one = doc.build(settings.config_dir, tmp_path / "a", analyze("CV Robotics Engineer", ctx), [])
    two = doc.build(settings.config_dir, tmp_path / "b", analyze("CV Robotics Engineer, 2 pages", ctx), [])
    assert len(two.cv["projects"]) >= len(one.cv["projects"]) and two.pages <= 2


@needs_tex
def test_telegram_cv_command(settings, sessions):
    bot, fake = _bot(settings, sessions)
    with sessions() as s:
        f = ProfileStore(s).propose("MATLAB / Simulink", kind="skill").fact
        ProfileStore(s).decide(f.id, USER_CONFIRMED)
        s.commit()
    bot.cvb.generate("CV pour un stage Tech Sales chez Salesforce à Dublin", background=False)
    texts = "\n".join(m["text"] for m in fake.sent)
    assert "Analyse en cours" in texts and "APPLICATION READY" in texts and "Rapport d'optimisation" in texts
    assert "Salesforce" in texts and "Ireland" in texts
    assert len(fake.docs) == 2
    _say(bot, "/cv")
    assert "Envoie-moi une offre" in fake.sent[-1]["text"]
