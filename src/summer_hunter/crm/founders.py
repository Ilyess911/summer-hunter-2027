"""Daily founders to contact: French people who run a small company abroad (Bali, South-East Asia, Gulf).

Flow, once a day after the morning digest:
1. Budget check (llm_usage purpose `founder_scout`, its own daily cap in config/founders.yaml).
2. One Claude call with server-side web search + web fetch: the model proposes candidates as JSON, each with a
   source URL and a verbatim quote naming the person.
3. Our own verification: we fetch every source page ourselves and keep a person only if the page contains both the
   full name and the company. Login-walled sources (LinkedIn, Instagram...) are refused. Already-proposed people
   are refused. Nothing the model says is kept without that check.
4. Kept people become CRM contacts (category founder_abroad, evidence = the source page) with a drafted message.
5. Telegram: one card per person, copy button for the message, "contacted" / "not for me" buttons.

Nothing is ever sent to the founders.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape
from typing import Any
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select

from summer_hunter.crm.validation import normalize_name
from summer_hunter.database.models import ContactEvidence, CRMContact, LLMUsage
from summer_hunter.database.repository import Repository
from summer_hunter.engine.geo import country_name
from summer_hunter.utils.settings import load_yaml
from summer_hunter.utils.text import contains_quote, html_to_text, normalize_for_match

log = logging.getLogger(__name__)

PURPOSE = "founder_scout"
CATEGORY = "founder_abroad"
TZ = ZoneInfo("Europe/Paris")
KV_DAY = "founders:day:"          # founders:day:<YYYY-MM-DD> -> JSON list of contact ids (the day's list)
KV_SENT = "founders:sent:"        # founders:sent:<YYYY-MM-DD> -> JSON list of ids whose card reached Telegram ("h" = header)
MIN_RUN_ESTIMATE_USD = 0.15       # a first run (no history) is admitted only with this much room left
LINKEDIN_LIMIT = 300

SCHEMA_HINT = """{"founders": [{
  "name": "full name as written on the source page",
  "company": "company name as written on the source page",
  "city": "city", "country_code": "ISO2",
  "sector": "short sector",
  "team_size": "what the source says about size, or 'unknown'",
  "what_they_do": "one sentence, in French, from the source only (max 140 chars)",
  "why_fit": "one sentence, in French: why an engineering student in business development / ops / automation could intern there (max 160 chars)",
  "hook": "a short French noun phrase about the company, taken from the source, used in a message (max 80 chars), e.g. 'votre studio de branding à Canggu'",
  "source_url": "https URL of the page that names this person as founder or co-founder of this company",
  "source_quote": "verbatim sentence from that page containing the person's name (max 220 chars)",
  "website": "company website or empty"
}]}"""


@dataclass
class Candidate:
    name: str
    company: str
    city: str
    country_code: str
    sector: str = ""
    team_size: str = ""
    what_they_do: str = ""
    why_fit: str = ""
    hook: str = ""
    source_url: str = ""
    source_quote: str = ""
    website: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Candidate":
        def s(key: str, limit: int) -> str:
            v = d.get(key)
            return re.sub(r"\s+", " ", str(v)).strip()[:limit] if isinstance(v, (str, int, float)) else ""

        return cls(name=s("name", 120), company=s("company", 160), city=s("city", 80),
                   country_code=s("country_code", 2).upper(), sector=s("sector", 80), team_size=s("team_size", 80),
                   what_they_do=s("what_they_do", 200), why_fit=s("why_fit", 220), hook=s("hook", 100),
                   source_url=s("source_url", 600), source_quote=s("source_quote", 300), website=s("website", 300))


@dataclass
class Verdict:
    candidate: Candidate
    ok: bool
    reason: str = ""
    excerpt: str = ""
    city_verified: bool = False
    website_verified: bool = False


@dataclass
class ScoutResult:
    status: str                                   # sent | skipped | budget | disabled | error | empty
    kept: list[int] = field(default_factory=list)  # contact ids
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    cost_usd: float = 0.0
    searches: int = 0
    note: str = ""


# Config, budget -------------------------------------------------------------------------------------

def config(config_dir) -> dict[str, Any]:  # noqa: ANN001
    return load_yaml(config_dir / "founders.yaml") or {}


def today() -> date:
    return datetime.now(TZ).date()


def spent(session, day: date | None = None) -> float:  # noqa: ANN001
    d = (day or today()).isoformat()
    return float(session.scalar(select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0))
                                .where(LLMUsage.local_date == d, LLMUsage.purpose == PURPOSE)) or 0.0)


def areas_for(day: date, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Two areas a day, rotating through the configured order (duplicates = more frequent)."""
    areas = cfg.get("areas") or []
    if not areas:
        return []
    i = (day.toordinal() * 2) % len(areas)
    picked = [areas[i]]
    for k in range(1, len(areas)):
        nxt = areas[(i + k) % len(areas)]
        if nxt["name"] != picked[0]["name"]:
            picked.append(nxt)
            break
    return picked


def known_names(session) -> list[str]:  # noqa: ANN001
    rows = session.execute(select(CRMContact.name, CRMContact.company).where(CRMContact.category == CATEGORY)).all()
    return [f"{n} ({c})" for n, c in rows]


# Prompt and model call --------------------------------------------------------------------------------

def build_prompt(cfg: dict[str, Any], areas: list[dict[str, Any]], exclude: list[str], want: int) -> tuple[str, str]:
    system = (
        "You research real people for a student's networking list. You use web search and web fetch, and you only "
        "report people you actually found on a public web page during this task. Never invent or guess a name, a "
        "company, a city or a quote. If you cannot find enough people, return fewer: an empty list is acceptable.\n"
        "Who: French founders or co-founders (French nationality or clearly French, e.g. interviewed by French media "
        "abroad, French chamber of commerce member, 'entrepreneur français à ...') who run a SMALL company "
        f"({cfg.get('company_size', '1-50 people')}) based in the requested area, still active.\n"
        "Good sources: the company's own about/team page, interviews in French expatriate media (lepetitjournal.com, "
        "French chambers of commerce, French Tech chapters, podcasts with show notes), local press. "
        "A source must be a page that a plain HTTP client can read: never LinkedIn, Instagram, Facebook, TikTok or X.\n"
        "Strategy, to find many people with few searches: favour pages that name SEVERAL French entrepreneurs at once "
        "(expat media portrait series, 'entrepreneurs français à <city>' articles, chamber of commerce member spotlights, "
        "French Tech chapter pages, podcast episode lists), then return every qualifying person on them. Search result "
        "snippets are usually enough; fetch a page only when a snippet does not show both the name and the company.\n"
        "The source_quote must be copied character for character from the source page and contain the person's name.\n"
        "Finish with ONE fenced ```json block and nothing after it, matching:\n" + SCHEMA_HINT
    )
    lines = [f"- {a['name']}: {', '.join(a.get('cities') or [])}" for a in areas]
    user = (
        f"Find up to {want + 3} such founders in these areas (spread across them):\n" + "\n".join(lines) + "\n\n"
        f"Preferred sectors: {', '.join(cfg.get('sectors') or [])}.\n"
        f"The student would join for an internship (April to August 2027) in: {', '.join(cfg.get('roles_for_candidate') or [])}. "
        "Prefer companies where a small team could take an intern: agencies, brands, hospitality, wellness, startups.\n"
        "Already proposed, do not return them again:\n" + ("\n".join(f"- {n}" for n in exclude[-150:]) or "- (none)")
    )
    return system, user


def _text_of(content) -> str:  # noqa: ANN001
    return "".join(getattr(b, "text", "") or "" for b in content if getattr(b, "type", "") == "text")


def parse_candidates(text: str) -> list[Candidate]:
    """The last ```json block of the answer (else the last {...} object)."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    raw = blocks[-1] if blocks else None
    if raw is None:
        start, end = text.rfind('{"founders"'), text.rfind("}")
        raw = text[start:end + 1] if start >= 0 and end > start else None
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = data.get("founders") if isinstance(data, dict) else None
    return [Candidate.from_dict(d) for d in items if isinstance(d, dict)] if isinstance(items, list) else []


class PartialCallError(RuntimeError):
    """A research call failed after some paid requests: the spend so far is carried so it is always recorded."""

    def __init__(self, msg: str, usage: tuple[float, int, int, int]) -> None:
        super().__init__(msg)
        self.usage = usage


def request_cost(tokens_in: int, tokens_out: int, searches: int, cfg: dict[str, Any], price: dict[str, Any]) -> float:
    return tokens_in / 1e6 * float(price["input"]) + tokens_out / 1e6 * float(price["output"]) \
        + searches * float(cfg.get("web_search_usd", 0.01))


def call_model(api_key: str, cfg: dict[str, Any], system: str, user: str, llm_pricing: dict[str, Any],  # noqa: ANN201
               remaining_usd: float = 1e9):
    """Research call with server-side web tools, resumed on pause_turn only while the spend leaves room under
    `remaining_usd`. Returns (text, cost_usd, searches, tokens_in, tokens_out); a failure after paid requests raises
    PartialCallError carrying that spend."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=300.0, max_retries=2)
    model = cfg.get("model", "claude-opus-5-5")
    price = (llm_pricing.get("pricing") or {}).get(model) or llm_pricing.get("default_pricing") or {"input": 4.0, "output": 20.0}
    blocked = list(cfg.get("blocked_source_domains") or [])[:64]
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": int(cfg.get("max_searches", 6)), "blocked_domains": blocked},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": int(cfg.get("max_fetches", 3)), "blocked_domains": blocked},
    ]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    tokens_in = tokens_out = searches = 0
    text = ""
    first_cost = None
    for _ in range(1 + int(cfg.get("max_continuations", 1))):
        try:
            resp = client.messages.create(
                model=model, max_tokens=int(cfg.get("max_tokens", 6000)), system=system, messages=messages,
                tools=tools, output_config={"effort": cfg.get("effort", "low")},
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            usage = (request_cost(tokens_in, tokens_out, searches, cfg, price), searches, tokens_in, tokens_out)
            raise PartialCallError(type(exc).__name__, usage) from exc
        u = resp.usage
        tokens_in += (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) \
            + (getattr(u, "cache_read_input_tokens", 0) or 0)
        tokens_out += u.output_tokens or 0
        stu = getattr(u, "server_tool_use", None)
        searches += int(getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        text = _text_of(resp.content)
        cost = request_cost(tokens_in, tokens_out, searches, cfg, price)
        first_cost = cost if first_cost is None else first_cost
        if resp.stop_reason == "pause_turn":
            if cost + first_cost > remaining_usd:     # another request like the first would cross the cap
                break
            messages = [{"role": "user", "content": user}, {"role": "assistant", "content": resp.content}]
            continue
        if resp.stop_reason == "refusal":
            text = ""
        break
    return text, request_cost(tokens_in, tokens_out, searches, cfg, price), searches, tokens_in, tokens_out


# Verification ---------------------------------------------------------------------------------------

ROLE = re.compile(r"\b(co-?founders?|founders?|founded|co-?founded|cofondat(eur|rice)s?|fondat(eur|rice)s?|a fonde|ont fonde|"
                  r"cree par|a cree|founding|owner|proprietaire|gerant|ceo|managing director|started|launched|lance par|a lance)\b")
WINDOW = 160            # characters around the full name in which the company and the founder/leader role must appear
# Known limit: the host is resolved once to refuse private addresses, then httpx resolves again (DNS rebinding not
# covered). Acceptable for a personal tool fetching pages the search returned; revisit if the fetcher is exposed.


def _company_core(company: str) -> str:
    c = normalize_for_match(company)
    c = re.sub(r"\b(pt|ltd|llc|co|fz|fze|fzco|dmcc|sdn bhd|pte|inc|sarl|sas|limited|company|group)\b\.?", " ", c)
    return re.sub(r"\s+", " ", c).strip(" .,-&")


def _found(term: str, text: str) -> bool:
    return bool(term) and re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


def check_page(c: Candidate, page_text: str) -> tuple[bool, str, str]:
    """Pure check on fetched text: the full name, written as one phrase, with the company AND a founder/owner role
    within WINDOW characters of it. Returns (ok, reason, excerpt taken from the page)."""
    norm = normalize_for_match(page_text)
    name = normalize_for_match(c.name)
    if len([t for t in re.split(r"[\s\-']+", name) if len(t) >= 2]) < 2:
        return False, "nom incomplet (prénom et nom requis)", ""
    core = _company_core(c.company)
    if len(core) < 3:
        return False, "nom d'entreprise trop court", ""
    hits = [m.start() for m in re.finditer(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", norm)]
    if not hits:
        return False, "nom absent de la page source", ""
    if not _found(core, norm):
        return False, "entreprise absente de la page source", ""
    for i in hits:
        window = norm[max(0, i - WINDOW):i + len(name) + WINDOW]
        if _found(core, window) and ROLE.search(window):
            return True, "", window[:600]
    return False, "rôle de fondateur non établi près du nom", ""


MAX_BYTES = 2_000_000


def _public_host(host: str) -> bool:
    import ipaddress
    import socket

    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


def _safe_url(url: str, cfg: dict[str, Any], resolve: bool = True) -> str | None:
    """Reason the URL may not be fetched, or None. HTTPS on 443, public addresses only, no login-walled sites."""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        return "source absente ou non https"
    if u.port not in (None, 443) or u.username or u.password:
        return "port ou identifiants dans l'URL refusés"
    host = u.hostname.lower()
    if any(host == d or host.endswith("." + d) for d in cfg.get("blocked_source_domains") or []):
        return f"source illisible sans compte ({host})"
    if resolve and not _public_host(host):
        return "adresse non publique refusée"
    return None


def fetch_text(url: str, user_agent: str, cfg: dict[str, Any], transport: httpx.BaseTransport | None = None,
               max_redirects: int = 3, resolve: bool = True) -> str:
    """GET with every redirect target re-checked, no proxy from the environment, and a hard byte limit."""
    with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False, headers={"User-Agent": user_agent},
                      transport=transport) as h:
        for _ in range(max_redirects + 1):
            reason = _safe_url(url, cfg, resolve)
            if reason:
                raise ValueError(reason)
            with h.stream("GET", url) as r:
                if r.is_redirect:
                    url = str(r.url.join(r.headers.get("location", "")))
                    continue
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "html" not in ctype and "text" not in ctype:
                    raise ValueError(f"not a web page ({ctype[:40]})")
                if int(r.headers.get("content-length") or 0) > MAX_BYTES:
                    raise ValueError("page trop lourde")
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise ValueError("page trop lourde")
                return html_to_text(body.decode(r.encoding or "utf-8", errors="replace"))
        raise ValueError("trop de redirections")


def verify(c: Candidate, cfg: dict[str, Any], excluded_countries: set[str], seen: set[str], user_agent: str,
           transport: httpx.BaseTransport | None = None) -> Verdict:
    if not c.name or not c.company:
        return Verdict(c, False, "nom ou entreprise manquant")
    if normalize_name(c.name) in seen:
        return Verdict(c, False, "déjà proposé")
    if c.country_code in excluded_countries:
        return Verdict(c, False, f"pays exclu ({c.country_code})")
    resolve = transport is None          # tests inject a transport and fake hosts: no DNS there
    reason = _safe_url(c.source_url, cfg, resolve)
    if reason:
        return Verdict(c, False, reason)
    try:
        text = fetch_text(c.source_url, user_agent, cfg, transport, resolve=resolve)
    except Exception as exc:  # noqa: BLE001 - any fetch failure = unverified = dropped
        return Verdict(c, False, f"page source inaccessible ({type(exc).__name__})")
    ok, reason, excerpt = check_page(c, text)
    v = Verdict(c, ok, reason, excerpt)
    if ok:   # what the page does not say is not shown as fact
        norm = normalize_for_match(text)
        v.city_verified = bool(c.city) and _found(normalize_for_match(c.city), norm)
        site = urlparse(c.website)
        v.website_verified = site.scheme == "https" and bool(site.hostname) and (
            site.hostname.removeprefix("www.") == (urlparse(c.source_url).hostname or "").removeprefix("www.")
            or site.hostname.removeprefix("www.") in norm)
    return v


# Message -------------------------------------------------------------------------------------------

def draft_message(c: Candidate, pitches: dict[str, Any], city_verified: bool = False) -> str:
    """French LinkedIn-length message (<= 300 chars) from verified facts only: the name and company found on the
    source page, the city only if the page names it, and the candidate's verified pitches. Never sent automatically."""
    first = c.name.split()[0]
    esilv = ((pitches.get("esilv") or {}).get("fr") or {}).get("short", "Je suis étudiant ingénieur en dernière année à l'ESILV")
    where = f" à {c.city}" if city_verified and c.city else ""
    ask = "Seriez-vous ouvert(e) à un court échange ?"
    body = (f"{esilv}, apprenti chez Air France Industries, et j'ai cofondé une agence B2B. "
            f"Je cherche un stage d'avril à août 2027{where}, dans une petite équipe.")
    after_comma = body[:1].lower() + body[1:]          # "Bonjour Thomas, je suis…" (no capital after a comma)
    for msg in (f"Bonjour {first}, j'ai découvert {c.company} et votre parcours. {body} {ask}",
                f"Bonjour {first}, {after_comma} {ask}"):
        if len(msg) <= LINKEDIN_LIMIT:
            return msg
    return f"Bonjour {first}, {after_comma} {ask}"[:LINKEDIN_LIMIT]


def linkedin_search(c: Candidate) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{c.name} {c.company}")


# Storage -------------------------------------------------------------------------------------------

def store(session, v: Verdict, message: str) -> CRMContact:  # noqa: ANN001
    c = v.candidate
    notes = json.dumps({"city": c.city, "city_verified": v.city_verified, "country": c.country_code, "sector": c.sector,
                        "team_size": c.team_size, "what": c.what_they_do, "why": c.why_fit,
                        "website": c.website if v.website_verified else "", "message": message,
                        "linkedin_search": linkedin_search(c)}, ensure_ascii=False)
    row = CRMContact(company=c.company, name=c.name, normalized_name=normalize_name(c.name), position="Fondateur·rice",
                     contact_type="Other", interaction_status="TO_CONTACT", source=c.source_url[:300], category=CATEGORY,
                     verification="verified", dismissed=False, notes=notes, next_action="Envoyer le message LinkedIn")
    session.add(row)
    session.flush()
    session.add(ContactEvidence(contact_id=row.id, kind="company_site", url=c.source_url[:1000], excerpt=v.excerpt[:600],
                                verified=True))
    return row


def notes_of(row: CRMContact) -> dict[str, Any]:
    try:
        return json.loads(row.notes or "{}")
    except json.JSONDecodeError:
        return {}


# Telegram ------------------------------------------------------------------------------------------

def card(i: int, row: CRMContact) -> tuple[str, list[list[dict[str, Any]]]]:
    n = notes_of(row)
    host = (urlparse(row.source).hostname or "source").removeprefix("www.")
    place = ", ".join(x for x in (n.get("city"), country_name(n.get("country")) if n.get("country") else "") if x)
    if not n.get("city_verified"):
        place += " (lieu non confirmé par la page)"
    links = [f'<a href="{escape(row.source, True)}">source ({escape(host)})</a>']
    if (n.get("website") or "").startswith("https://"):
        links.append(f'<a href="{escape(n["website"], True)}">site</a>')
    links.append(f'<a href="{escape(n.get("linkedin_search", ""), True)}">LinkedIn</a>')
    text = (f"<b>{i}. {escape(row.name)}</b> · {escape(row.company)}\n📍 {escape(place)}"
            + (f" · {escape(n['sector'])}" if n.get("sector") else "")
            + (f" · taille : {escape(n['team_size'])}" if n.get("team_size") and n["team_size"] != "unknown" else "")
            + (f"\n{escape(n['what'])}" if n.get("what") else "")
            + (f"\n<i>Pourquoi : {escape(n['why'])}</i>" if n.get("why") else "")
            + "\n<i>Vérifié sur la page : nom, entreprise et rôle de fondateur ou dirigeant. Le reste = résumé IA, à confirmer.</i>"
            + "\n" + " · ".join(links)
            + f"\n\n<code>{escape(n.get('message', ''))}</code>")
    buttons = [[{"text": "📋 Copier le message", "copy_text": {"text": (n.get("message") or "")[:256]}}],
               [{"text": "✓ Contacté", "callback_data": f"fd:{row.id}:sent"},
                {"text": "✕ Pas pour moi", "callback_data": f"fd:{row.id}:no"}]]
    return text, buttons


def send_day(telegram, chat_id: str, session, ids: list[int], header: str | None, on_sent=None) -> None:  # noqa: ANN001
    """Header then one card per contact. `on_sent(key)` is called after each successful message ("h" or the id)."""
    if header:
        telegram.send_message(chat_id, header)
        if on_sent:
            on_sent("h")
    for i, cid in enumerate(ids, 1):
        row = session.get(CRMContact, cid)
        if row is not None:
            text, buttons = card(i, row)
            telegram.send_message(chat_id, text, buttons)
            if on_sent:
                on_sent(cid)


def _header(day: date, cfg: dict[str, Any], n: int, want: int, cost: float | None) -> str:
    short = f" (seulement {n} vérifiés sur {want} demandés)" if n < want else ""
    return (f"<b>🌴 Fondateurs à contacter · {day.strftime('%d/%m')}</b>{short}\n"
            f"Zones : {escape(', '.join(a['name'] for a in areas_for(day, cfg)))}. "
            "Chaque personne est vérifiée sur sa page source : nom, entreprise et rôle de fondateur ou dirigeant. "
            "Rien n'est envoyé à personne." + (f" Coût : {cost:.2f} $." if cost is not None else ""))


def deliver(settings, sessions, telegram, day: date, cfg: dict[str, Any], cost: float | None = None) -> int:  # noqa: ANN001
    """Send what has not reached Telegram yet for `day` (resumes after a partial failure). Returns cards sent."""
    if telegram is None or not settings.telegram_chat_id:
        return 0
    with sessions() as s:
        repo = Repository(s)
        ids = json.loads(repo.get_kv(KV_DAY + day.isoformat()) or "[]")
        done = json.loads(repo.get_kv(KV_SENT + day.isoformat()) or "[]")
        todo = [i for i in ids if i not in done]
        if not todo:
            return 0

        def on_sent(key) -> None:  # noqa: ANN001
            done.append(key)
            repo.set_kv(KV_SENT + day.isoformat(), json.dumps(done))
            s.commit()

        header = None if "h" in done else _header(day, cfg, len(ids), int(cfg.get("per_day", 6)), cost)
        send_day(telegram, str(settings.telegram_chat_id), s, todo, header, on_sent)
        return len(todo)


# Main ----------------------------------------------------------------------------------------------

def run(settings, sessions, telegram=None, force: bool = False, dry_run: bool = False,  # noqa: ANN001, PLR0913
        model_call=None, transport: httpx.BaseTransport | None = None) -> ScoutResult:
    """The daily job. `model_call(system, user) -> (text, cost, searches, tin, tout)` is injectable for tests."""
    cfg = config(settings.config_dir)
    if not cfg.get("enabled", False):
        return ScoutResult("disabled")
    import fcntl

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with open(settings.data_dir / "founders.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return ScoutResult("skipped", note="une recherche est déjà en cours")
        return _run(settings, sessions, cfg, telegram, force, dry_run, model_call, transport)


def _run(settings, sessions, cfg, telegram, force, dry_run, model_call, transport) -> ScoutResult:  # noqa: ANN001, PLR0913
    day = today()
    want = int(cfg.get("per_day", 6))
    with sessions() as s:
        repo = Repository(s)
        already = repo.get_kv(KV_DAY + day.isoformat()) is not None
    if already and not force:
        try:
            n = 0 if dry_run else deliver(settings, sessions, telegram, day, cfg)
        except Exception as exc:  # noqa: BLE001 - the next run resumes again
            log.warning("founders delivery failed: %s", type(exc).__name__)
            return ScoutResult("error", note="envoi Telegram interrompu, reprise au prochain passage")
        return ScoutResult("resumed" if n else "skipped", note="liste du jour déjà faite" + (f", {n} cartes renvoyées" if n else ""))
    with sessions() as s:
        cap = float(cfg.get("daily_budget_usd", 0.30))
        # A paid run cannot be stopped midway: it starts only if a run like the last one (at least
        # MIN_RUN_ESTIMATE_USD) still fits under today's cap; pause_turn resumes are cut against the same room.
        last = s.scalar(select(LLMUsage.cost_usd).where(LLMUsage.purpose == PURPOSE, LLMUsage.outcome == "ok")
                        .order_by(LLMUsage.id.desc()).limit(1)) or 0.0
        remaining = cap - spent(s, day)
        if remaining < max(float(last), MIN_RUN_ESTIMATE_USD):
            return ScoutResult("budget", note=f"plafond {cap:.2f} $ atteint aujourd'hui")
        exclude = known_names(s)
        seen = {normalize_name(n.rsplit(" (", 1)[0]) for n in exclude}
    if model_call is None:
        if not settings.anthropic_api_key:
            return ScoutResult("error", note="ANTHROPIC_API_KEY absente")
        llm_cfg = settings.llm

        def model_call(system: str, user: str):  # noqa: ANN202
            return call_model(settings.anthropic_api_key, cfg, system, user, llm_cfg, remaining)

    system, user = build_prompt(cfg, areas_for(day, cfg), exclude, want)
    outcome, text, cost, searches, tin, tout = "ok", "", 0.0, 0, 0, 0
    try:
        text, cost, searches, tin, tout = model_call(system, user)
    except PartialCallError as exc:          # paid requests happened before the failure: their spend is recorded
        outcome = "error"
        cost, searches, tin, tout = exc.usage
        log.warning("founder scout call failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 - the morning never fails on this
        outcome = "error"
        log.warning("founder scout call failed: %s", type(exc).__name__)
    finally:
        with sessions() as s:
            s.add(LLMUsage(local_date=day.isoformat(), provider="anthropic", model=str(cfg.get("model")), purpose=PURPOSE,
                           input_tokens=tin, output_tokens=tout, cost_usd=cost, outcome=outcome))
            s.commit()
    if outcome == "error":
        return ScoutResult("error", cost_usd=cost, note="appel de recherche échoué")
    try:   # local trace of what the model answered, to tune the search (never sent anywhere)
        logs = settings.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / f"founders-{day.isoformat()}.txt").write_text(text, encoding="utf-8")
    except OSError:
        pass

    geo = settings.preferences.get("geography", {}) or {}
    excluded = {c.upper() for c in geo.get("excluded_countries", [])} | {c.upper() for c in geo.get("safety_excluded_countries", [])}
    pitches = (load_yaml(settings.config_dir / "networking.yaml") or {}).get("pitches") or {}
    result = ScoutResult("empty", cost_usd=round(cost, 4), searches=searches)
    kept: list[Verdict] = []
    for c in parse_candidates(text):
        if len(kept) >= want:
            break
        v = verify(c, cfg, excluded, seen, settings.user_agent, transport)
        if v.ok:
            kept.append(v)
            seen.add(normalize_name(c.name))
        else:
            result.rejected.append((c.name or "?", v.reason))
    if dry_run:
        result.status = "dry_run"
        result.note = f"{len(kept)} vérifiés : " + "; ".join(f"{v.candidate.name} ({v.candidate.company})" for v in kept)
        return result
    with sessions() as s:
        ids = []
        for v in kept:
            if s.execute(select(CRMContact.id).where(CRMContact.company == v.candidate.company,
                                                     CRMContact.normalized_name == normalize_name(v.candidate.name))).first():
                result.rejected.append((v.candidate.name, "déjà dans le CRM"))
                continue
            ids.append(store(s, v, draft_message(v.candidate, pitches, v.city_verified)).id)
        repo = Repository(s)
        repo.set_kv(KV_DAY + day.isoformat(), json.dumps(ids))
        repo.set_kv(KV_SENT + day.isoformat(), "[]")      # a forced re-run delivers its new list from scratch
        s.commit()
    result.kept = ids
    if not ids:
        result.status = "empty"
        return result
    if telegram is None or not settings.telegram_chat_id:
        result.status = "stored"
        return result
    try:
        deliver(settings, sessions, telegram, day, cfg, cost)
        result.status = "sent"
    except Exception as exc:  # noqa: BLE001 - stored; the next run (night or /founders) resumes the unsent cards
        log.warning("founders delivery failed: %s", type(exc).__name__)
        result.status, result.note = "stored", "envoi Telegram interrompu, reprise au prochain passage"
    return result


def mark(session, contact_id: int, action: str) -> str | None:  # noqa: ANN001
    """Buttons: sent -> MESSAGE_SENT (follow-up in 5 business days via the CRM), no -> dismissed."""
    row = session.get(CRMContact, contact_id)
    if row is None or row.category != CATEGORY:
        return None
    if action == "sent":
        row.interaction_status, row.first_contact_date = "MESSAGE_SENT", row.first_contact_date or today()
        row.last_interaction_date, row.next_action = today(), "Relancer si pas de réponse"
        return "Noté : contacté"
    if action == "no":
        row.dismissed, row.interaction_status = True, "CLOSED"
        return "Retiré de ta liste"
    return None
