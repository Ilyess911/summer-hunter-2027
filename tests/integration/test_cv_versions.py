"""AI CV Builder lot 4: append-only versions, history, retrieval."""

from __future__ import annotations

import pytest

from summer_hunter.cv_builder.intent import CVRequest, analyze, context_from
from summer_hunter.cv_builder.versions import VersionStore, label
from summer_hunter.database.migrations import init_db, make_engine
from tests.integration.test_cv_document import needs_tex
from tests.integration.test_cv_profile import _bot, _press, _say


@pytest.fixture
def vs(settings, sessions):
    with sessions() as s:
        yield VersionStore(s, settings.data_dir, settings.config_dir), context_from(settings, s), s


def test_request_round_trips(vs):
    _, ctx, _ = vs
    req = analyze("Solutions Engineer Intern\nAcme - Munich, Germany\n\nRequirements\n- Python required\n" + "x " * 300, ctx)
    again = CVRequest.from_dict(req.to_dict())
    assert again == req and again.requirements and again.requirements[0].name == req.requirements[0].name


@needs_tex
def test_versions_are_append_only(vs):
    store, ctx, s = vs
    v1, res1 = store.create(analyze("CV pour un stage Robotics Engineer à Singapour", ctx), [])
    s.commit()
    assert (v1.lineage_id, v1.version, label(v1)) == (v1.id, 1, f"CV-{v1.id:04d} v1")
    assert res1.pdf_path.name == "CV_Robotics_Engineer_Singapore_2027_v1.pdf" and f"CV-{v1.id:04d}/v1" in str(res1.pdf_path)
    v2, res2 = store.derive(v1, [], "en français, 2 pages", language="fr", pages=2)
    v3, _ = store.derive(v2, [], "mets en avant le jumeau numérique", emphasis=["jumeau numérique digital twin"])
    s.commit()
    assert [v.version for v in store.lineage(v1.id)] == [1, 2, 3]
    assert v2.parent_id == v1.id and v3.parent_id == v2.id and v3.lineage_id == v1.id
    assert v2.language == "fr" and v2.request["pages"] == 2 and v2.request["language_reason"] == "modification demandée"
    assert res2.pdf_path.name.endswith("_v2.pdf")
    assert all(p.exists() for v in (v1, v2, v3) for p in store.files(v))        # v1 files untouched
    assert v3.request["emphasis"][0].startswith("jumeau")
    assert v1.profile["yaml_sha1"] and v1.content["experiences"] and v1.source_text.startswith("CV pour")


@needs_tex
def test_history_shows_latest_version_of_each_cv(vs):
    store, ctx, s = vs
    a, _ = store.create(analyze("Tech Sales", ctx), [])
    b, _ = store.create(analyze("CV Robotics Engineer", ctx), [])
    a2, _ = store.derive(a, [], "2 pages", pages=2)
    s.commit()
    assert [(r.lineage_id, r.version) for r in store.history()] == [(a.id, 2), (b.id, 1)]


def test_derive_refuses_unknown_fields(vs):
    store, ctx, _ = vs
    from types import SimpleNamespace

    parent = SimpleNamespace(request=analyze("Tech Sales", ctx).to_dict(), id=1, lineage_id=1)
    with pytest.raises(ValueError):
        store.derive(parent, [], "x", source_text="injected")


@needs_tex
def test_a_rolled_back_build_never_blocks_the_next_one(settings, sessions):
    with sessions() as s:
        store = VersionStore(s, settings.data_dir, settings.config_dir)
        store.create(analyze("Tech Sales", context_from(settings, s)), [])
        s.rollback()                                 # row gone, files left behind
    with sessions() as s:
        store = VersionStore(s, settings.data_dir, settings.config_dir)
        row, res = store.create(analyze("Tech Sales", context_from(settings, s)), [])
        s.commit()
        assert res.pdf_path.exists()
    assert list((settings.data_dir / "cv_builder").glob("CV-*/v1.orphan-*"))


@needs_tex
def test_versions_survive_a_restart(settings, sessions):
    with sessions() as s:
        row, _ = VersionStore(s, settings.data_dir, settings.config_dir).create(analyze("Tech Sales", context_from(settings, s)), [])
        s.commit()
        rid = row.id
    reopened = init_db(make_engine(settings.database_url))
    with reopened() as s:
        assert VersionStore(s, settings.data_dir, settings.config_dir).get(rid).stem.endswith("_v1")


@needs_tex
def test_telegram_history_and_retrieval(settings, sessions):
    bot, fake = _bot(settings, sessions)
    _say(bot, "/cv_history")
    assert "Aucun CV" in fake.sent[-1]["text"]
    bot.cvb.generate("CV pour un stage Tech Sales chez Salesforce à Dublin", background=False)
    assert "CV-0001 v1" in "\n".join(m["text"] for m in fake.sent)
    with sessions() as s:
        store = VersionStore(s, settings.data_dir, settings.config_dir)
        store.derive(store.get(1), [], "2 pages", pages=2)
        s.commit()
    _say(bot, "/cv_history")
    assert "CV-0001 v2" in fake.sent[-1]["text"] and "Salesforce" in fake.sent[-1]["text"]
    _press(bot, "cvh:1")
    out = fake.sent[-1]["text"]
    assert "v1" in out and "v2" in out and "2 pages" in out
    docs = len(fake.docs)
    _press(bot, "cvf:1")
    assert len(fake.docs) == docs + 2 and "CV-0001 v1" in fake.sent[-1]["text"]
    _say(bot, "/cv_history 1")
    assert "CV-0001" in fake.sent[-1]["text"]
    _press(bot, "cvf:999")
    assert "introuvable" in fake.sent[-1]["text"]
