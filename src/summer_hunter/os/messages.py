"""Personalized LinkedIn messages, built from evidence and checked before they are shown.

Every message carries at least one verified element beyond the name and company (a public post
tied to the opening, the vacancy that names the recruiter, a shared school or employer the user
confirmed, the person's team owning the role) or it is flagged "limited personalization". Facts
about the candidate come only from the verified pitches in config/networking.yaml. Nothing is
sent: the user copies the text, sends it, and marks it sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from summer_hunter.database.models import ContactEvidence, CRMApplication, CRMContact, Interaction, Opportunity

KINDS = ["connection_request", "initial", "before_applying", "after_applying", "referral_request", "alumni", "follow_up",
         "thank_you", "post_interview"]
VARIANTS = ["default", "shorter", "more_direct", "more_formal", "more_natural"]
SHORT_KINDS = {"connection_request"}
FOLLOW_KINDS = {"follow_up", "thank_you", "post_interview"}


@dataclass
class Draft:
    text: str
    kind: str
    variant: str
    language: str
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    personalization: str = "full"          # full | limited
    validation: dict[str, Any] = field(default_factory=dict)


def _role(title: str) -> str:
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", title or "").strip()
    t = re.sub(r"^(summer )?20\d\d\s*(intern(ship)?)?\s*[-–:]\s*", "", t, flags=re.I)
    t = re.sub(r"\s+-\s+.*$", "", t)
    t = re.sub(r"\b(summer|spring|fall)?\s*(intern(ship)?|stagiaire|stage|praktikum|co-?op)\b[,]?", "", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–:,")
    return t[:70] or "this"


def _hook(contact: CRMContact, opp: Opportunity | None, evidence: list[ContactEvidence], lang: str) -> tuple[str | None, dict | None]:
    """The single strongest verified reason this message is for this person."""
    fr = lang == "fr"
    linked = [e for e in evidence if e.verified and opp is not None and e.opportunity_id == opp.id]
    post = next((e for e in linked if e.kind == "public_post"), None)
    role = _role(opp.title) if opp else ""
    if post:
        return ((f"J'ai vu votre post sur le stage {role}." if fr else f"I saw your post about the {role} internship."),
                {"kind": "public_post", "url": post.url, "excerpt": post.excerpt})
    vac = next((e for e in linked if e.kind == "vacancy"), None)
    if vac:
        return ((f"Vous êtes indiqué(e) comme recruteur sur l'offre {role}." if fr
                 else f"You are listed as the recruiter on the {role} posting."), {"kind": "vacancy", "url": vac.url})
    stated = [e for e in evidence if e.verified and e.kind in {"user", "public_post", "profile"}]
    if contact.category == "alumni_esilv" and stated:
        return (("Je suis moi aussi passé par l'ESILV." if fr else "I am also an ESILV student, a few years behind you."),
                {"kind": "shared_school", "source": stated[0].kind})
    if contact.category == "alumni_af_airbus" and stated:
        return (("Nous avons un parcours commun dans l'aéronautique (Air France / Airbus)." if fr
                 else "We share an aerospace background (Air France / Airbus)."), {"kind": "shared_employer", "source": stated[0].kind})
    if opp and contact.category in {"hiring_manager", "team_manager"} and linked:
        return ((f"Votre équipe recrute un(e) stagiaire {role}." if fr else f"Your team is hiring a {role} intern."),
                {"kind": "team_owns_role", "source": linked[0].kind})
    return None, None


def _pitch(cfg: dict[str, Any], key: str, lang: str, form: str = "full") -> str:
    entry = ((cfg.get("pitches") or {}).get(key) or {}).get(lang) or {}
    return entry.get(form, "") if isinstance(entry, dict) else str(entry)


def _pitches(cfg: dict[str, Any], track: str | None, lang: str) -> tuple[str, str, str, str]:
    p = cfg.get("pitches") or {}
    lead = (p.get("lead_by_track") or {}).get(track or "business_tech", "air_france_industries")
    second = (p.get("second_by_track") or {}).get(track or "business_tech", "nexya_agency")
    return lead, second, _pitch(cfg, lead, lang), _pitch(cfg, second, lang)


ORG_NAME = {"air_france_industries": "Air France Industries", "airbus_helicopters": "Airbus Helicopters", "nexya_agency": "Nexya Agency"}
FOCUS_FR = {"data analysis": "l'analyse de données", "kpi": "le suivi des KPI", "automation": "l'automatisation",
            "process improvement": "l'amélioration des processus", "continuous improvement": "l'amélioration continue",
            "reliability": "la fiabilité", "discovery": "l'identification des besoins clients", "customer needs": "les besoins clients"}
FOCUS_PATTERNS = {"data analysis": r"data analy|analyse de(s)? donn", "kpi": r"\bkpis?\b|indicateurs de performance",
                  "automation": r"automati[sz]", "process improvement": r"process improvement|amélioration des processus",
                  "continuous improvement": r"continuous improvement|amélioration continue", "reliability": r"reliabilit|fiabilit",
                  "discovery": r"discovery calls?|customer discovery", "customer needs": r"customer needs|besoins (des )?clients"}


def _focus(opp: Opportunity | None, cfg: dict[str, Any], allowed: set[str], lang: str, lead: str = "") -> tuple[str | None, dict | None]:
    """A responsibility the posting states, linked to a verified experience the message already mentions."""
    if opp is None or not opp.description_text:
        return None, None
    text = opp.description_text.lower()
    links = cfg.get("focus_links") or {}
    for term, exp in links.items():
        if exp in allowed and re.search(FOCUS_PATTERNS.get(term, r"(?<![a-z])" + re.escape(term) + r"(?![a-z])"), text):
            label = FOCUS_FR.get(term, term) if lang == "fr" else ("KPIs" if term == "kpi" else term)
            if exp == lead:                 # the experience was just named: do not repeat it
                line = (f"L'offre met l'accent sur {label}, un point central de mon travail actuel." if lang == "fr"
                        else f"The posting stresses {label}, which is a core part of my work there.")
            else:
                org = ORG_NAME[exp]
                line = (f"L'offre met l'accent sur {label}, une grande partie de mon expérience chez {org}." if lang == "fr"
                        else f"The posting stresses {label}, a large part of my experience at {org}.")
            return line, {"kind": "posting_focus", "term": term, "experience": exp}
    return None, None


def language_for(opp: Opportunity | None, cfg: dict[str, Any]) -> str:
    fr = set((cfg.get("messages") or {}).get("french_countries", []))
    return "fr" if opp is not None and opp.country_code in fr else "en"


def compose(kind: str, contact: CRMContact, opp: Opportunity | None, app: CRMApplication | None, evidence: list[ContactEvidence],
            interactions: list[Interaction], cfg: dict[str, Any], variant: str = "default", lang: str | None = None) -> Draft:
    if kind not in KINDS:
        raise ValueError(f"message kind must be one of: {', '.join(KINDS)}")
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of: {', '.join(VARIANTS)}")
    lang = lang or language_for(opp, cfg)
    fr = lang == "fr"
    short = variant == "shorter"
    first = (contact.name or "").split()[0] if contact.name else ""
    hello = f"Bonjour {first}," if fr else (f"Dear {first}," if variant == "more_formal" else f"Hi {first},")
    role = _role(opp.title) if opp else ""
    company = opp.company_name if opp else contact.company
    the_role = (f"le stage {role}" if role != "this" else "ce stage") if fr else (f"the {role} internship" if role != "this" else "this internship")
    hook, ref = _hook(contact, opp, evidence, lang)
    lead_key, second_key, lead, second = _pitches(cfg, opp.career_track if opp else None, lang)
    me = _pitch(cfg, "esilv", lang, "short" if short else "full")
    lead_line = _pitch(cfg, lead_key, lang, "short" if short else "full")
    focus, focus_ref = (None, None) if short else _focus(opp, cfg, {lead_key, second_key}, lang, lead_key)
    applied = app is not None and app.date_applied is not None
    replied = any(i.kind == "reply" for i in interactions)
    sent = sorted((i for i in interactions if i.kind == "message_sent"), key=lambda i: i.occurred_at)
    sign = ("Bien cordialement,\nIlyess Assadi" if fr else "Best regards,\nIlyess Assadi") if variant == "more_formal" else "Ilyess"
    refs = [r for r in (ref, focus_ref) if r]
    if kind == "referral_request" and not replied:
        kind = "alumni"                     # never ask a stranger for a referral in a first message

    def para(*parts: str | None) -> str:
        return " ".join(x.strip().rstrip(".") + "." for x in parts if x)

    if kind == "connection_request":
        reason = hook or ((f"J'ai postulé à {the_role} chez {company}." if fr else f"I applied for {the_role} at {company}.") if applied
                          else (f"Je m'intéresse à {the_role} chez {company}." if fr else f"I'm interested in {the_role} at {company}."))
        ask = "Puis-je vous ajouter à mon réseau ?" if fr else "Would you be open to connecting?"
        intro = _pitch(cfg, "esilv", lang, "short")
        text = f"{hello} {reason} {ask}" if short else f"{hello} {para(reason, intro)} {ask}"
        if len(text) > int((cfg.get("messages") or {}).get("connection_request_max_chars", 250)):
            text = f"{hello} {reason} {ask}"
    elif kind in {"initial", "before_applying"}:
        reason = hook or (f"Je prépare ma candidature pour {the_role} chez {company}." if fr
                          else f"I'm preparing my application for {the_role} at {company}.")
        ask = ("Pourrais-je vous poser deux questions sur ce que l'équipe attend d'un stagiaire sur ce poste ?" if fr
               else "Could I ask you two quick questions about what the team expects from an intern in this role?")
        body = para(me, lead_line, focus)
        text = (f"{hello}\n\n{ask} {para(reason)}\n\n{body}\n\n{sign}" if variant == "more_direct"
                else f"{hello}\n\n{para(reason)} {body}\n\n{ask}\n\n{sign}")
    elif kind == "after_applying":
        when = (f" le {app.date_applied:%d/%m}" if fr else f" on {app.date_applied:%d %B}") if applied else ""
        did = f"J'ai postulé à {the_role}{when}." if fr else f"I applied for {the_role}{when}."
        if hook:                            # the hook already names the role: do not repeat it
            did = f"J'ai postulé{when}." if fr else f"I applied{when}."
        ask = ("Si un élément peut faciliter l'examen de mon dossier, je vous l'envoie volontiers." if fr
               else "If anything would help you review my application, I'm happy to send it.")
        text = f"{hello}\n\n{para(hook, did, me, lead_line, focus)}\n\n{ask}\n\n{sign}"
    elif kind in {"alumni", "referral_request"}:
        why = f"Je regarde {the_role} chez {company}." if fr else f"I'm looking at {the_role} at {company}."
        if kind == "referral_request":
            ask = ("Après notre échange, seriez-vous à l'aise pour me recommander ? Aucun souci si ce n'est pas le cas." if fr
                   else "After our exchange, would you be comfortable referring me? No problem at all if not.")
        else:
            ask = ("Auriez-vous 15 minutes dans les semaines à venir pour me dire comment l'équipe travaille ?" if fr
                   else "Would you have 15 minutes in the coming weeks to tell me how the team works?")
        text = f"{hello}\n\n{para(hook, why, me, lead_line)}\n\n{ask}\n\n{sign}"
    elif kind == "follow_up":
        prev = (f" du {sent[-1].occurred_at:%d/%m}" if fr else f" from {sent[-1].occurred_at:%d %B}") if sent else ""
        text = ((f"{hello}\n\nJe reviens vers vous au sujet de mon message{prev} sur {the_role}. Auriez-vous un moment cette "
                 f"semaine ? Je serai bref.\n\n{sign}") if fr else
                (f"{hello}\n\nFollowing up on my note{prev} about {the_role}. Would you have a few minutes this week? "
                 f"I'll keep it short.\n\n{sign}"))
    elif kind == "thank_you":
        text = ((f"{hello}\n\nMerci pour votre réponse sur {the_role}, elle m'aide beaucoup. Je ne reviendrai vers vous que si une "
                 f"question précise se pose.\n\n{sign}") if fr else
                (f"{hello}\n\nThank you for your reply about {the_role}, it really helps. I'll only come back to you if a "
                 f"specific question comes up.\n\n{sign}"))
    else:   # post_interview
        org = {"air_france_industries": "Air France Industries", "airbus_helicopters": "Airbus Helicopters",
               "nexya_agency": "Nexya Agency"}.get(lead_key, "ESILV")
        text = ((f"{hello}\n\nMerci pour l'entretien sur {the_role}. Il a confirmé mon intérêt, en particulier le lien avec mon "
                 f"travail chez {org}. Je reste disponible pour toute information.\n\n{sign}") if fr else
                (f"{hello}\n\nThank you for the interview for {the_role}. It confirmed my interest, especially the link with my "
                 f"work at {org}. I remain available for anything you need.\n\n{sign}"))
    if variant == "more_natural" and not fr:
        text = (text.replace("Could I ask you two quick questions about what the team expects from an intern in this role?",
                             "Would you be up for two quick questions on what the team looks for in an intern?")
                .replace("Would you be open to connecting?", "Happy to connect if that works for you.")
                .replace("If anything would help you review my application, I'm happy to send it.",
                         "If it helps your review, I can send anything you need.")
                .replace("Would you have 15 minutes in the coming weeks to tell me how the team works?",
                         "Could we grab 15 minutes in the next few weeks so you can tell me how the team works?"))
    if variant == "more_formal" and not fr:
        text = text.replace("I'm ", "I am ").replace("I'll ", "I will ")
    text = re.sub(r"[ \t]+", " ", text).replace(" \n", "\n").strip()
    draft = Draft(text=text, kind=kind, variant=variant, language=lang, evidence_refs=refs,
                  personalization="full" if refs else "limited")
    draft.validation = validate(draft, contact, app, evidence, interactions, cfg)
    return draft


def _org(pitch: str) -> str:
    for org in ("Air France Industries", "Airbus Helicopters", "Nexya Agency"):
        if org in pitch:
            return org
    return "ESILV"


ALLOWED_ORGS = {"esilv", "air france industries", "airbus helicopters", "nexya agency", "air france", "airbus", "linkedin"}


def validate(d: Draft, contact: CRMContact, app: CRMApplication | None, evidence: list[ContactEvidence],
             interactions: list[Interaction], cfg: dict[str, Any]) -> dict[str, Any]:
    mc = cfg.get("messages") or {}
    text, low = d.text, d.text.lower()
    issues: list[str] = []
    chars, words = len(text), len(re.findall(r"\b[\w'’-]+\b", text))
    if d.kind in SHORT_KINDS:
        if chars > int(mc.get("connection_request_max_chars", 250)):
            issues.append(f"{chars} characters, over the {mc.get('connection_request_max_chars', 250)} limit")
    elif d.kind in FOLLOW_KINDS:
        lo, hi = mc.get("follow_up_words", [30, 70])
        if not lo - 10 <= words <= hi + 10:
            issues.append(f"{words} words, expected about {lo}-{hi}")
    else:
        lo, hi = mc.get("initial_words", [60, 110])
        if not (lo - 15 <= words <= hi + 15 or d.variant == "shorter" and words >= 30):
            issues.append(f"{words} words, expected about {lo}-{hi}")
    for phrase in mc.get("banned_phrases", []):
        if phrase in low:
            issues.append(f"generic phrase: '{phrase}'")
    if "—" in text:
        issues.append("em dash")
    grams = re.findall(r"\b\w+\b", low)
    four = [" ".join(grams[i:i + 4]) for i in range(len(grams) - 3)]
    if len(four) != len(set(four)):
        issues.append("repeated wording")
    if ("your post" in low or "votre post" in low) and not any(e.kind == "public_post" and e.verified for e in evidence):
        issues.append("mentions a post that was not retrieved")
    if ("an esilv student, a few years" in low or "passé par l'esilv" in low) and contact.category != "alumni_esilv":
        issues.append("claims a shared school without evidence")
    if ("i applied" in low or "j'ai postulé" in low) and not (app and app.date_applied):
        issues.append("claims an application that is not recorded")
    if ("referring me" in low or "recommander" in low) and not any(i.kind == "reply" for i in interactions):
        issues.append("referral request to someone who has not replied yet")
    if "?" not in text and d.kind not in {"thank_you", "post_interview", "after_applying"}:
        issues.append("no clear request")
    caps = set(re.findall(r"\b(?:[A-Z][\w'’&-]*\s?){2,4}(?:Industries|Helicopters|Agency)\b", text))
    for org in caps:
        if org.strip().lower() not in ALLOWED_ORGS:
            issues.append(f"unverified organisation: {org.strip()}")
    return {"ok": not issues, "issues": issues, "chars": chars, "words": words,
            "personalization": d.personalization, "checked": ["facts", "length", "repetition", "tone", "purpose", "appropriateness"]}


def suggest_kind(timing_kind: str | None, contact: CRMContact, app: CRMApplication | None, interactions: list[Interaction]) -> str:
    if timing_kind:
        return timing_kind
    if any(i.kind == "message_sent" for i in interactions):
        return "follow_up"
    if app and app.date_applied:
        return "after_applying"
    return "before_applying" if contact.category in {"hiring_manager", "team_manager", "team_member"} else "connection_request"


def today_iso() -> str:
    return date.today().isoformat()
