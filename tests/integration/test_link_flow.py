"""Send-a-link flow: ATS link parsing, LinkedIn refusal, pasted text, end-to-end bot behaviour."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from summer_hunter.collectors.link_ingest import NeedsText, fetch_posting, first_url, from_pasted_text, parse_header_line
from summer_hunter.utils.http import HostPolicy, PoliteClient
from tests.conftest import FILLER

LEVER_ID = "6ed76ce8-4156-4b60-b120-403538bd66cd"


def api(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if u.endswith("/robots.txt"):
        return httpx.Response(404)
    if u == f"https://api.lever.co/v0/postings/acme/{LEVER_ID}":
        return httpx.Response(200, json={"text": "Solutions Engineer Intern", "descriptionPlain": FILLER, "lists": [],
                                         "categories": {"location": "Singapore"}, "country": "SG",
                                         "hostedUrl": f"https://jobs.lever.co/acme/{LEVER_ID}"})
    if u == "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/42":
        return httpx.Response(200, json={"title": "Product Marketing Intern", "company_name": "Stripe",
                                         "location": {"name": "Dublin, Ireland"}, "content": "&lt;p&gt;" + FILLER + "&lt;/p&gt;"})
    if u == "https://careers.example.com/job/9":
        html = ('<html><script type="application/ld+json">{"@type":"JobPosting","title":"BD Intern",'
                '"hiringOrganization":{"name":"Example AG"},"jobLocation":{"address":{"addressLocality":"Zurich","addressCountry":"CH"}},'
                f'"description":"{FILLER}"}}</script></html>')
        return httpx.Response(200, text=html)
    if u == "https://careers.example.com/gone":
        return httpx.Response(404)
    raise AssertionError(f"unexpected request {u}")


def read(url):
    async def go():
        async with PoliteClient("t", transport=httpx.MockTransport(api), default_policy=HostPolicy(min_interval=0), max_retries=0) as h:
            return await fetch_posting(h, url, None)
    return asyncio.run(go())


def test_ats_and_jsonld_links():
    assert read(f"https://jobs.lever.co/acme/{LEVER_ID}").country_hint == "SG"
    gh = read("https://boards.greenhouse.io/stripe/jobs/42")
    assert gh.company == "Stripe" and "<p>" not in gh.description_text
    page = read("https://careers.example.com/job/9")
    assert page.company == "Example AG" and page.country_hint == "CH"


def test_linkedin_and_dead_links_ask_for_text():
    with pytest.raises(NeedsText, match="LinkedIn"):
        read("https://www.linkedin.com/jobs/view/4012345678/")
    with pytest.raises(NeedsText, match="404"):
        read("https://careers.example.com/gone")


def test_pasted_text_with_header_line():
    raw = from_pasted_text("https://www.linkedin.com/jobs/view/1/", "Sales Engineer Intern | Acme | Singapore\n" + FILLER, None)
    assert (raw.title, raw.company, raw.location_text) == ("Sales Engineer Intern", "Acme", "Singapore")
    assert parse_header_line("just text") is None
    assert first_url("regarde ça https://jobs.lever.co/x/y, merci") == "https://jobs.lever.co/x/y"


def test_bot_link_flow_sends_card_cv_and_letter(settings, sessions, monkeypatch):
    from summer_hunter.bot import links
    from summer_hunter.collectors.link_ingest import _raw
    from summer_hunter.integrations.telegram_client import TelegramClient

    sent = []

    def tg(request):
        method = request.url.path.rsplit("/", 1)[-1]
        sent.append(method)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    settings.telegram_bot_token, settings.telegram_chat_id = "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ", "42"
    client = TelegramClient(settings.telegram_bot_token, transport=httpx.MockTransport(tg))

    async def fake_fetch(http, url, provider):
        return _raw(url, "Solutions Engineer Intern", "Acme", "Singapore", FILLER + " From June 1 to August 20, 2027.")

    letters = []
    monkeypatch.setattr(links, "fetch_posting", fake_fetch)
    monkeypatch.setattr(links, "_local_model", lambda s: None)
    monkeypatch.setattr("summer_hunter.web.letter.run_letter_job", lambda *a, **k: letters.append(a[3]))
    links.process(settings, sessions, client, "https://jobs.example.com/1")
    assert sent.count("sendDocument") == 2 and letters        # CV pdf + tex, then the letter job
    sent.clear()
    letters.clear()

    async def paris(http, url, provider):
        return _raw(url, "Solutions Engineer Intern", "Beta", "Paris, France", FILLER)

    monkeypatch.setattr(links, "fetch_posting", paris)
    links.process(settings, sessions, client, "https://jobs.example.com/2")
    assert "sendDocument" not in sent and not letters             # rejected: asks before generating


def test_split_paste_is_read_as_one_posting_and_its_inner_link_is_not_fetched(settings, sessions, monkeypatch):
    from summer_hunter.bot import links
    from summer_hunter.bot.handlers import Bot
    from summer_hunter.integrations.telegram_client import TelegramClient

    settings.telegram_bot_token, settings.telegram_chat_id = "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ", "42"
    client = TelegramClient(settings.telegram_bot_token,
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})))
    calls = []
    monkeypatch.setattr(links, "start", lambda s, f, t, url, text=None: calls.append((url, text)))
    bot = Bot(settings, sessions, client)
    bot.paste_wait_s = 60                      # flushed by hand below
    part1 = "Supply Chain Intern\n" + FILLER
    part2 = "More on our benefits: https://www.pgcareers.com/benefits " + FILLER
    for i, text in enumerate((part1, part2), 1):
        bot.handle_update({"update_id": i, "message": {"chat": {"id": 42}, "text": text}})
    assert calls == []                         # nothing fetched, nothing read yet
    bot._paste_timer.cancel()
    bot.flush_paste()
    assert calls == [(None, part1.strip() + "\n" + part2.strip())]
    calls.clear()
    bot.handle_update({"update_id": 3, "message": {"chat": {"id": 42}, "text": "regarde https://jobs.lever.co/x/y"}})
    assert calls == [("https://jobs.lever.co/x/y", None)]   # a bare link is still read as a link


def test_pasted_text_never_takes_a_pitch_sentence_as_title_or_company():
    from summer_hunter.collectors.link_ingest import _title_line, plausible_company
    text = "Start date: the internship runs from June to August.\nGraduate Supply Chain Internship\nWe are P&G."
    assert _title_line(text) == "Graduate Supply Chain Internship"
    assert not plausible_company("one of the most innovative startups in France")
    assert plausible_company("Pasqal") and plausible_company("eBay")
    with pytest.raises(NeedsText):
        from_pasted_text(None, "Research shows that women might feel hesitant to apply.\n" + FILLER, None)
