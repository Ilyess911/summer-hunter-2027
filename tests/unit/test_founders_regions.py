"""Region priority (Asia > Middle East > Europe > Africa) and the daily founders list (22 Nov 2026)."""

from __future__ import annotations

import json

import httpx

from summer_hunter.crm import founders
from summer_hunter.database.models import ContactEvidence, CRMContact, LLMUsage
from summer_hunter.database.repository import Repository
from summer_hunter.engine import priority as gpriority
from summer_hunter.engine.compensation import CompensationReport
from summer_hunter.engine.regions import region_bonus_map, region_rank

PAGE = """<html><body><h1>About us</h1><p>Studio Kelapa was founded in 2019 by Camille Durand, a French designer
who moved to Canggu. Studio Kelapa is a branding studio of 8 people.</p></body></html>"""


def _answer(*people: dict) -> str:
    return "Here is what I found.\n```json\n" + json.dumps({"founders": list(people)}) + "\n```"


def _person(**kw) -> dict:
    base = {"name": "Camille Durand", "company": "Studio Kelapa", "city": "Canggu", "country_code": "ID",
            "sector": "branding", "team_size": "8 people", "what_they_do": "Studio de branding à Canggu.",
            "why_fit": "Petite équipe qui gère des clients internationaux.", "hook": "votre studio de branding à Canggu",
            "source_url": "https://studiokelapa.example/about",
            "source_quote": "Studio Kelapa was founded in 2019 by Camille Durand", "website": "https://studiokelapa.example"}
    base.update(kw)
    return base


def _transport(pages: dict[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(str(request.url))
        if body is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})
    return httpx.MockTransport(handler)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    def send_message(self, chat_id, text, buttons=None, disable_preview=True):  # noqa: ANN001
        self.sent.append((text, buttons))
        return len(self.sent)


# Regions ------------------------------------------------------------------------------------------

def test_region_order_follows_user_choice(settings):
    geo = settings.preferences["geography"]
    bonus = region_bonus_map(geo)
    # 27 Nov 2026: the six off-market countries first, then the earlier order.
    assert bonus["JP"][1] == bonus["CA"][1] == bonus["CH"][1] == bonus["SG"][1] > bonus["ID"][1] > bonus["AE"][1]
    assert bonus["AE"][1] > bonus["DE"][1] > bonus["MA"][1] == 0 > bonus["US"][1]
    assert bonus["US"][1] < 0
    assert region_rank("VN", geo) < region_rank("QA", geo) < region_rank("NL", geo) < region_rank("KE", geo)


def test_region_bonus_moves_global_priority():
    comp = CompensationReport(status="COMPENSATION_UNKNOWN", confidence="UNKNOWN")
    args = (70, 60, 40, comp, False, 60.0, 50.0, 60.0, 30, "conversion", {}, {})
    base = gpriority.compute(*args)
    asia = gpriority.compute(*args, region=("South-East Asia & Greater China", 10.0))
    assert asia.score == round(base.score + 10, 1)
    assert any("Région prioritaire" in n for n in asia.notes)


# Founders: verification ---------------------------------------------------------------------------

def test_check_page_needs_full_name_and_company():
    c = founders.Candidate.from_dict(_person())
    assert founders.check_page(c, founders.html_to_text(PAGE))[0]
    other = founders.Candidate.from_dict(_person(name="Julien Martin"))
    assert founders.check_page(other, founders.html_to_text(PAGE))[1] == "nom absent de la page source"
    wrong_co = founders.Candidate.from_dict(_person(company="Bali Surf House"))
    assert not founders.check_page(wrong_co, founders.html_to_text(PAGE))[0]
    first_only = founders.Candidate.from_dict(_person(name="Camille"))
    assert not founders.check_page(first_only, founders.html_to_text(PAGE))[0]


def test_verify_refuses_login_walls_excluded_countries_and_dead_pages(settings):
    cfg = founders.config(settings.config_dir)
    t = _transport({})
    li = founders.Candidate.from_dict(_person(source_url="https://www.linkedin.com/in/camille"))
    assert "illisible" in founders.verify(li, cfg, set(), set(), "ua", t).reason
    assert founders.verify(founders.Candidate.from_dict(_person()), cfg, set(), set(), "ua",
                           _transport({"https://studiokelapa.example/about": PAGE})).ok
    fr = founders.Candidate.from_dict(_person(country_code="FR"))
    assert "exclu" in founders.verify(fr, cfg, {"FR"}, set(), "ua", t).reason
    dead = founders.Candidate.from_dict(_person())
    assert "inaccessible" in founders.verify(dead, cfg, set(), set(), "ua", t).reason
    http = founders.Candidate.from_dict(_person(source_url="http://studiokelapa.example/about"))
    assert not founders.verify(http, cfg, set(), set(), "ua", t).ok


def test_message_fits_linkedin_and_stays_neutral():
    c = founders.Candidate.from_dict(_person(company="X" * 80))
    msg = founders.draft_message(c, {}, city_verified=True)
    assert len(msg) <= founders.LINKEDIN_LIMIT
    assert msg.startswith("Bonjour Camille, je suis")
    assert "ouvert(e)" in msg and "avril à août 2027" in msg


def test_areas_rotate_two_distinct_per_day(settings):
    from datetime import date, timedelta

    cfg = founders.config(settings.config_dir)
    seen = set()
    for k in range(14):
        picked = founders.areas_for(date(2026, 11, 22) + timedelta(days=k), cfg)
        assert len(picked) == 2 and picked[0]["name"] != picked[1]["name"]
        seen |= {a["name"] for a in picked}
    assert {"Australia", "Japan", "Canada", "South Korea", "Singapore", "Switzerland"} <= seen


# Founders: end to end ------------------------------------------------------------------------------

def test_run_keeps_only_verified_people_and_sends_cards(settings, sessions):
    answer = _answer(_person(), _person(name="Julien Martin", company="Ghost Agency",
                                        source_url="https://ghost.example/team", source_quote="Julien Martin founded"))
    tg = FakeTelegram()
    settings.telegram_chat_id = "1"
    res = founders.run(settings, sessions, tg, model_call=lambda s, u: (answer, 0.12, 4, 30000, 2000),
                       transport=_transport({"https://studiokelapa.example/about": PAGE}))
    assert res.status == "sent" and len(res.kept) == 1
    assert res.rejected == [("Julien Martin", "page source inaccessible (HTTPStatusError)")]
    with sessions() as s:
        row = s.get(CRMContact, res.kept[0])
        assert row.category == "founder_abroad" and row.verification == "verified"
        assert s.query(ContactEvidence).filter_by(contact_id=row.id, verified=True).count() == 1
        usage = s.query(LLMUsage).filter_by(purpose="founder_scout").one()
        assert usage.cost_usd == 0.12
        # the founder budget never eats the analysis/email budget
        assert Repository(s).spend_on(usage.local_date) == 0.0
    assert "décideurs à contacter" in tg.sent[0][0] and "seulement 1" in tg.sent[0][0]
    text, buttons = tg.sent[1]
    assert "Camille Durand" in text and buttons[1][0]["callback_data"].startswith("fd:")

    again = founders.run(settings, sessions, tg, model_call=lambda s, u: (answer, 0.1, 1, 1, 1))
    assert again.status == "skipped"


def test_run_respects_daily_cap_and_never_repeats_a_person(settings, sessions):
    with sessions() as s:
        s.add(LLMUsage(local_date=founders.today().isoformat(), provider="anthropic", model="m", purpose="founder_scout",
                       cost_usd=0.31))
        s.commit()
    called = []
    res = founders.run(settings, sessions, None, model_call=lambda s, u: called.append(1))
    assert res.status == "budget" and not called

    with sessions() as s:
        s.query(LLMUsage).delete()
        s.add(CRMContact(company="Studio Kelapa", name="Camille Durand", normalized_name="camille durand",
                         contact_type="Other", interaction_status="TO_CONTACT", source="x", category="founder_abroad"))
        s.commit()
    prompts = []

    def call(system, user):  # noqa: ANN001, ANN202
        prompts.append(user)
        return _answer(_person()), 0.05, 1, 1, 1

    res = founders.run(settings, sessions, None, model_call=call,
                       transport=_transport({"https://studiokelapa.example/about": PAGE}))
    assert "Camille Durand (Studio Kelapa)" in prompts[0]
    assert res.kept == [] and res.rejected == [("Camille Durand", "déjà proposé")]


def test_buttons_mark_contacted_or_dismissed(settings, sessions):
    with sessions() as s:
        row = CRMContact(company="A", name="B C", normalized_name="b c", contact_type="Other",
                         interaction_status="TO_CONTACT", source="x", category="founder_abroad")
        s.add(row)
        s.flush()
        assert founders.mark(s, row.id, "sent") == "Noté : contacté"
        assert row.interaction_status == "MESSAGE_SENT" and row.first_contact_date
        assert founders.mark(s, row.id, "no") and row.dismissed


def test_parse_candidates_tolerates_noise():
    assert founders.parse_candidates("no json here") == []
    assert founders.parse_candidates('```json\n{"founders": "oops"}\n```') == []
    two = founders.parse_candidates(_answer(_person(), _person(name="Léa Morel")))
    assert [c.name for c in two] == ["Camille Durand", "Léa Morel"]


# Review fixes (Codex, 22 Nov 2026) --------------------------------------------------------------------

def test_name_and_company_without_founder_link_is_refused():
    page = ("<p>Camille Durand joined our advisory board this year.</p>" + "<p>" + "lorem ipsum " * 60 + "</p>"
            "<p>Studio Kelapa was founded by Jean Dupont.</p>")
    c = founders.Candidate.from_dict(_person())
    ok, reason, _ = founders.check_page(c, founders.html_to_text(page))
    assert not ok and reason == "rôle de fondateur non établi près du nom"
    scattered = founders.Candidate.from_dict(_person(name="Durand Camille"))
    assert not founders.check_page(scattered, founders.html_to_text(PAGE))[0]


def test_unsafe_urls_are_never_fetched(monkeypatch, settings):
    cfg = founders.config(settings.config_dir)
    for url in ("https://127.0.0.1/x", "https://example.test:8443/x", "https://user:pw@example.test/x",
                "http://example.test/x"):
        assert founders._safe_url(url, cfg, resolve=True), url
    monkeypatch.setattr(founders, "_public_host", lambda host: host != "internal.example")
    assert founders._safe_url("https://internal.example/a", cfg) == "adresse non publique refusée"

    def handler(request):  # noqa: ANN001, ANN202
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
    import pytest
    with pytest.raises(ValueError, match="non https"):
        founders.fetch_text("https://ok.example/a", "ua", cfg, httpx.MockTransport(handler), resolve=False)


def test_oversized_page_is_refused(settings):
    cfg = founders.config(settings.config_dir)
    big = httpx.MockTransport(lambda r: httpx.Response(200, content=b"a" * (founders.MAX_BYTES + 10),
                                                       headers={"content-type": "text/html"}))
    import pytest
    with pytest.raises(ValueError, match="lourde"):
        founders.fetch_text("https://ok.example/a", "ua", cfg, big, resolve=False)


def test_partial_failure_spend_is_recorded(settings, sessions):
    def call(system, user):  # noqa: ANN001, ANN202
        raise founders.PartialCallError("APIConnectionError", (0.14, 3, 20000, 1000))
    res = founders.run(settings, sessions, None, model_call=call)
    assert res.status == "error"
    with sessions() as s:
        assert founders.spent(s) == 0.14
    # 0.16 left < max(last ok cost, 0.15)? last ok is none -> estimate 0.15 still fits once
    assert founders.run(settings, sessions, None, model_call=lambda s, u: (_answer(), 0.15, 1, 1, 1)).status == "empty"
    assert founders.run(settings, sessions, None, force=True, model_call=lambda s, u: (_answer(), 0.1, 1, 1, 1)).status == "budget"


def test_interrupted_delivery_resumes_unsent_cards_only(settings, sessions):
    class Flaky(FakeTelegram):
        def __init__(self, fail_at):  # noqa: ANN001
            super().__init__()
            self.fail_at = fail_at

        def send_message(self, chat_id, text, buttons=None, disable_preview=True):  # noqa: ANN001
            if len(self.sent) + 1 == self.fail_at:
                self.fail_at = None
                raise RuntimeError("telegram down")
            return super().send_message(chat_id, text, buttons)

    settings.telegram_chat_id = "1"
    pages = {"https://studiokelapa.example/about": PAGE,
             "https://lea.example/about": "<p>Léa Morel est la fondatrice de Maison Lea à Ubud.</p>"}
    answer = _answer(_person(), _person(name="Léa Morel", company="Maison Lea", city="Ubud",
                                        source_url="https://lea.example/about", source_quote="", website="https://lea.example"))
    tg = Flaky(fail_at=3)                       # header ok, card 1 ok, card 2 fails
    res = founders.run(settings, sessions, tg, model_call=lambda s, u: (answer, 0.1, 1, 1, 1),
                       transport=_transport(pages))
    assert res.status == "stored" and len(res.kept) == 2 and len(tg.sent) == 2
    again = founders.run(settings, sessions, tg)
    assert again.status == "resumed" and len(tg.sent) == 3 and "Léa Morel" in tg.sent[2][0]
    assert founders.run(settings, sessions, tg).status == "skipped" and len(tg.sent) == 3


def test_unverified_city_and_website_are_not_presented_as_facts(settings, sessions):
    page = "<p>Studio Kelapa was founded by Camille Durand.</p>"
    res = founders.run(settings, sessions, None, model_call=lambda s, u: (_answer(_person(website="https://other.example")), 0.1, 1, 1, 1),
                       transport=_transport({"https://studiokelapa.example/about": page}))
    with sessions() as s:
        row = s.get(CRMContact, res.kept[0])
        n = founders.notes_of(row)
        assert n["website"] == "" and not n["city_verified"] and "Canggu" not in n["message"]
        assert "lieu non confirmé" in founders.card(1, row)[0]


def test_non_french_contact_gets_english_message_and_cto_role_counts():
    c = founders.Candidate.from_dict({"name": "Kenji Sato", "company": "Mujin Robotics", "city": "Tokyo",
                                      "country_code": "JP", "role": "co-founder & CTO", "french": False})
    msg = founders.draft_message(c, {}, city_verified=True)
    assert msg.startswith("Hi Kenji,") and "April to August 2027" in msg and len(msg) <= founders.LINKEDIN_LIMIT
    ok, _, _ = founders.check_page(c, "Kenji Sato is the CTO of Mujin Robotics in Tokyo.")
    assert ok
    fr = founders.Candidate.from_dict({"name": "Paul Durand", "company": "Robo SA", "city": "Lausanne",
                                       "country_code": "CH"})
    assert fr.french and founders.draft_message(fr, {}).startswith("Bonjour Paul,")


def test_company_that_refuses_interns_is_dropped():
    c = founders.Candidate.from_dict({"name": "Han Lee", "company": "Seoul Robotics", "website": ""})
    faq = ("Does Seoul Robotics offer internship programs? Seoul Robotics is not currently offering internship "
           "programs. Please note that we are not accepting internship-related inquiries at this time.")
    assert "stagiaires" in founders.no_interns_reason(c, {}, "ua", faq)
    assert founders.no_interns_reason(c, {}, "ua", "We hire interns every summer in Seoul.") == ""
    assert founders.no_interns_reason(c, {}, "ua", "Nous ne proposons pas de stage cette année.") != ""


def test_person_without_official_site_is_dropped(settings, sessions):
    page = "<p>Studio Kelapa was founded in 2019 by Camille Durand in Canggu.</p>"
    res = founders.run(settings, sessions, None, model_call=lambda s, u: (_answer(_person(website="")), 0.1, 1, 1, 1),
                       transport=_transport({"https://studiokelapa.example/about": page}))
    assert not res.kept and "site officiel manquant" in res.rejected[0][1]
