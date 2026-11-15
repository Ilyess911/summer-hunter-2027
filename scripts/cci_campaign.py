"""CCI France International campaign: 20 isolated applications (CV PDF + DOCX, email, research sheet, CRM draft).

    uv run python scripts/cci_campaign.py cv --wave 1            # CVs, context, research sheets, CRM drafts
    uv run python scripts/cci_campaign.py email --id 01          # validate and attach the email(s) written for 01
    uv run python scripts/cci_campaign.py verify                 # cross-campaign contamination and file checks
    uv run python scripts/cci_campaign.py summary                # README table + ZIP

Every application has its own ApplicationContext (context.json); its CV and email are built from it and checked
against it, and no file of an application may name another chamber of the campaign. Nothing is ever sent:
CRM records stay at PREPARING with the note "DRAFT — NOT SENT".
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from summer_hunter.cv_builder import application_email as ae  # noqa: E402
from summer_hunter.cv_builder import service  # noqa: E402
from summer_hunter.cv_builder.context import ContextViolation, from_request, validate_email  # noqa: E402
from summer_hunter.cv_builder.intent import analyze, context_from, detect_countries  # noqa: E402
from summer_hunter.cv_builder.profile import ProfileStore  # noqa: E402
from summer_hunter.cv_builder.versions import VersionStore, label  # noqa: E402
from summer_hunter.database.migrations import init_db, make_engine  # noqa: E402
from summer_hunter.utils.settings import Settings, load_yaml  # noqa: E402
from summer_hunter.utils.text import normalize_for_match  # noqa: E402

CFG = load_yaml(ROOT / "config" / "campaigns" / "cci_2027.yaml")
OUT = Path.home() / "Documents" / CFG["campaign"]
RESEARCH = Path(sys.argv[sys.argv.index("--research") + 1]) if "--research" in sys.argv else OUT / "_research"
EMAILS = OUT / "_emails"
STATE = OUT / "_state.json"
NOT = "Not publicly confirmed"


def state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def research(cid: str) -> dict:
    data = json.loads((RESEARCH / f"{cid}.json").read_text(encoding="utf-8"))
    if not data.get("official_name") or not data.get("website"):
        raise SystemExit(f"{cid}: research incomplete (official name or website missing)")
    # 'Main name (other name / acronym)': the main name addresses the documents, the others are kept for checks.
    full = data["official_name"]
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", full)
    if m:
        data["official_name"] = m.group(1).strip()
        extra = [x.strip() for x in re.split(r"\s*/\s*|\s+-\s+", m.group(2)) if x.strip()]
        data["former_or_other_names"] = list(dict.fromkeys([*(data.get("former_or_other_names") or []), *extra]))
    if " - " in data["official_name"]:                       # 'CCI France UAE - Emirats Arabes Unis' -> 'CCI France UAE'
        head, tail = data["official_name"].split(" - ", 1)
        data["official_name"] = head.strip()
        data["former_or_other_names"] = list(dict.fromkeys([*data["former_or_other_names"], tail.strip()]))
    if data.get("short_name") and "/" in data["short_name"]:  # 'CCIFM / MFCCI': two acronyms
        parts = [x.strip() for x in data["short_name"].split("/") if x.strip()]
        data["short_name"], data["acronyms"] = parts[0], parts
    else:
        data["acronyms"] = [data["short_name"]] if data.get("short_name") else []
    data["full_official_name"] = full
    return data


SHORT_FIELD = {"Market Entry & Expansion": "Market Entry", "Trade & Investment": "Trade and Investment",
               "Innovation & Technology": "Innovation and Technology"}


def role_for(r: dict) -> str:
    """'Business Development and Market Entry' (two fields the chamber's verified activities support)."""
    fields = [SHORT_FIELD.get(f, f) for f in r.get("best_matching_fields") or [] if f] or ["Business Development",
                                                                                          "International Business"]
    return " and ".join(fields[:2])


def names_of(r: dict) -> list[str]:
    out = [r["official_name"], *(r.get("former_or_other_names") or []), *(r.get("acronyms") or [])]
    return [n for n in out if n and len(n) >= 3]


def others(cid: str) -> dict[str, list[str]]:
    """Names that identify the OTHER chambers (contamination check): no place names, no notes, no generic words,
    and no name this chamber shares with another one ('CCI France Canada' for Montréal and Toronto)."""
    from summer_hunter.cv_builder.intent import is_place

    own = {normalize_for_match(n) for n in names_of(research(cid))}
    out = {}
    for c in CFG["chambers"]:
        if c["id"] != cid and (RESEARCH / f"{c['id']}.json").exists():
            out[c["id"]] = [n for n in names_of(research(c["id"]))
                            if "(" not in n and not is_place(n) and normalize_for_match(n) not in own
                            and not re.fullmatch(r"(?i)(cci france|french chamber( of commerce)?|chambre de commerce)", n)]
    return out


def mentions(text: str, name: str) -> bool:
    n = normalize_for_match(name)
    return bool(n) and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", normalize_for_match(text)) is not None


def pdf_text(path: Path) -> str:
    return subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True).stdout


def research_md(c: dict, r: dict, ctx: dict) -> str:
    def items(xs, key="fact"):  # noqa: ANN001, ANN202
        return "\n".join(f"- {x.get(key)} ([source]({x.get('source')}))" for x in xs or []) or f"- {NOT}"
    contacts = "\n".join(
        f"| {x.get('name')} | {x.get('role')} | {x.get('email') or NOT} | {x.get('linkedin') or NOT} | [source]({x.get('source')}) | {x.get('note') or ''} |"
        for x in r.get("contacts") or []) or f"| {NOT} | | | | | |"
    offers = "\n".join(f"- [{o.get('title')}]({o.get('url')}) (seen {o.get('date_seen')})" for o in (r.get("internships") or {}).get("published_offers") or []) \
        or "- No published internship offer found"
    g = r.get("general_contact") or {}
    return f"""# {r['official_name']} — research sheet ({ctx['application_id']})

| | |
|---|---|
| Official name | {r['official_name']}{f" ({r['short_name']})" if r.get('short_name') else ''} |
| Other names | {', '.join(r.get('former_or_other_names') or []) or '—'} |
| Legal identity | {r.get('legal_identity') or NOT} |
| Website | {r['website']} |
| Country / city | {r.get('country')} / {r.get('city')} |
| Offices | {', '.join(r.get('offices') or []) or NOT} |
| Working languages | {r.get('working_languages') or NOT} |
| General contact | {g.get('email') or NOT}{' · ' + g['phone'] if g.get('phone') else ''} ([source]({g.get('source')})) |
| Careers page | {r.get('careers_page') or NOT} |
| Priority / wave | {c['priority']} / {c['wave']} |

## What the chamber does (verified)
{items(r.get('activities'))}

## Sectors, committees, programmes
{items(r.get('sectors_or_programmes'))}

## Contacts (only names and addresses published by official sources)
| Name | Role | Email | LinkedIn | Source | Note |
|---|---|---|---|---|---|
{contacts}

## Internships
{offers}

Application conditions: {(r.get('internships') or {}).get('application_conditions') or NOT}

## Visa (published information only)
{(r.get('visa') or {}).get('info') or NOT}{f" ([source]({(r.get('visa') or {}).get('source')}))" if (r.get('visa') or {}).get('source') else ''}

## Pay (published information only)
{(r.get('pay') or {}).get('info') or NOT}{f" ([source]({(r.get('pay') or {}).get('source')}))" if (r.get('pay') or {}).get('source') else ''}

## Positioning chosen for this application
Fields: {', '.join(r.get('best_matching_fields') or [])} · CV emphasis: {', '.join(c['emphasis'])}

## To verify before sending
{chr(10).join('- ' + x for x in r.get('needs_verification') or []) or '- nothing flagged'}
"""


# CVs -----------------------------------------------------------------------------------------------

def cmd_cv(wave: int | None, only: str | None) -> None:
    settings = Settings.load()
    sessions = init_db(make_engine(settings.database_url))
    st = state()
    OUT.mkdir(parents=True, exist_ok=True)
    for c in CFG["chambers"]:
        if (wave and c["wave"] != wave) or (only and c["id"] != only):
            continue
        if not (RESEARCH / f"{c['id']}.json").exists():
            print(f"{c['id']} {c['folder']}: research missing, skipped")
            continue
        r = research(c["id"])
        cc = (detect_countries(r.get("country") or "") or detect_countries(r.get("city") or "") or [None])[0]
        role = role_for(r)
        text = (f"Candidature spontanée à la {r['official_name']} pour un stage {role}, {r.get('city')}, "
                f"{r.get('country')}, avril à août 2027, CV en anglais, une page.")
        with service.build_lock(settings.data_dir), sessions() as s:
            actx = context_from(settings, s)
            req = analyze(text, actx)
            req = replace(req, company=r["official_name"], company_aliases=list(r["acronyms"]),
                          company_source="explicit", country_code=cc, target_role=role, track="international",
                          mode="spontaneous", situation="spontaneous", language="en", pages=1, emphasis=c["emphasis"],
                          sector=["international business"])
            vs = VersionStore(s, settings.data_dir, settings.config_dir)
            previous = vs.get(st[c["id"]]["version_id"]) if c["id"] in st else None
            if previous is not None:     # same application: next version of the same CV, never a new one
                row, res = vs.derive(previous, ProfileStore(s).confirmed(), f"CCI campaign {c['id']} update",
                                     validator=service._guard(settings, s), target_role=role, company=r["official_name"],
                                     company_aliases=list(r["acronyms"]),
                                     country_code=cc, emphasis=c["emphasis"], track="international", language="en", pages=1)
            else:
                row, res = vs.create(req, ProfileStore(s).confirmed(), validator=service._guard(settings, s),
                                     change=f"CCI campaign {c['id']}")
            s.commit()
            vid, tag = row.id, label(row)
        folder = OUT / c["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        pdf = folder / f"CV_Ilyess_Assadi_{c['tag']}.pdf"
        docx = folder / f"CV_Ilyess_Assadi_{c['tag']}.docx"
        shutil.copy2(res.pdf_path, pdf)
        shutil.copy2(res.docx_path, docx)
        ctx = {"application_id": f"CCI27-{c['id']}", "cv_version": tag, "cv_version_id": vid,
               "organisation": r["official_name"], "short_name": r.get("short_name"), "country": r.get("country"),
               "country_code": cc, "city": r.get("city"), "website": r["website"],
               "recipient": (r.get("contacts") or [{}])[0].get("name") or "Team (general contact)",
               "application_type": "spontaneous (Option A: internship at the chamber; Option B: introduction to a member company)",
               "field": role, "period": CFG["period"], "language": "en" + (" + fr" if c.get("email_fr") else ""),
               "sources": sorted({x.get("source") for x in (r.get("activities") or []) if x.get("source")}),
               "prepared_on": date.today().isoformat(), "status": "DRAFT — NOT SENT"}
        (folder / "context.json").write_text(json.dumps(ctx, indent=2, ensure_ascii=False))
        (folder / f"Research_{c['tag']}.md").write_text(research_md(c, r, ctx), encoding="utf-8")
        app_id = crm_record(settings, sessions, c, r, ctx, pdf, st.get(c["id"], {}).get("crm_app_id"))
        st[c["id"]] = {"version_id": vid, "tag": tag, "crm_app_id": app_id, "folder": str(folder)}
        save_state(st)
        print(f"{c['id']} {c['folder']}: {tag} · {res.pages} page · checks {'OK' if res.ok else 'TO CHECK'} · CRM APP-{app_id:04d}")


def crm_record(settings, sessions, c: dict, r: dict, ctx: dict, pdf: Path, existing: int | None) -> int:  # noqa: ANN001
    from summer_hunter.crm.service import CRM
    from summer_hunter.database.repository import Repository
    from summer_hunter.integrations.anthropic_client import NullProvider
    from summer_hunter.models.opportunity import RawOpportunity
    from summer_hunter.pipeline import CycleStats, Hunter

    with sessions() as s:
        crm = CRM(s, settings.data_dir, settings.timezone)
        if existing and crm.application(existing):
            app = crm.application(existing)
        else:
            raw = RawOpportunity(source="manual", source_id=f"cci27-{c['id']}", url=r["website"], apply_url=None,
                                 title=f"Spontaneous application – {ctx['field']} internship", company=r["official_name"],
                                 location_text=f"{r.get('city')}, {r.get('country')}",
                                 description_text="Candidature spontanée préparée (campagne CCI France International 2027). DRAFT — NOT SENT.")
            opp = Hunter(settings, sessions, provider=NullProvider()).ingest(Repository(s), raw, CycleStats())
            app, _ = crm.track(opp, "cci_campaign", status="PREPARING")
        lead = (r.get("contacts") or [None])[0]
        visa = (r.get("visa") or {}).get("info") or NOT
        pay = (r.get("pay") or {}).get("info") or NOT
        notes = (f"DRAFT — NOT SENT · {ctx['application_id']} · {r['website']} · field: {ctx['field']} · "
                 f"availability {CFG['period']} · visa: {visa[:160]} · pay: {pay[:120]} · CV {pdf.name} · "
                 f"folder {pdf.parent}")
        crm.update_fields(app, {"notes": notes, "compensation": pay[:200] if pay != NOT else None,
                                "internship_start": date(2027, 4, 1), "internship_end": date(2027, 8, 31),
                                "recruiter_name": (lead or {}).get("name"),
                                "recruiter_linkedin": (lead or {}).get("linkedin") or None,
                                "next_action": "Relire puis envoyer le mail (aucun envoi automatique)"}, "cci_campaign")
        for x in r.get("contacts") or []:
            if x.get("name"):
                crm.add_contact(r["official_name"], x["name"], "cci_campaign", position=x.get("role"),
                                linkedin_url=x.get("linkedin") or None, application=app, contact_type="Other",
                                notes=f"email: {x.get('email') or NOT} · source: {x.get('source')}")
        s.commit()
        return app.id


# Emails --------------------------------------------------------------------------------------------

def parse_email(path: Path) -> tuple[str, str, list[str]]:
    text = path.read_text(encoding="utf-8").strip()
    m = re.match(r"(?:Subject|Objet)\s*:\s*(.+?)\n\n(.+)$", text, re.S)
    if not m:
        raise SystemExit(f"{path}: expected 'Subject: …' then a blank line and the body")
    subject, body = m.group(1).strip(), m.group(2).strip()
    blocks = body.split("\n\n")
    return subject, body, blocks[1:] if len(blocks) > 1 else blocks     # paragraphs without the greeting


def cmd_email(cid: str) -> None:
    from summer_hunter.cv_builder.email import _people, _signature

    settings = Settings.load()
    sessions = init_db(make_engine(settings.database_url))
    st = state()[cid]
    c = next(x for x in CFG["chambers"] if x["id"] == cid)
    r = research(cid)
    folder = Path(st["folder"])
    with sessions() as s:
        row = VersionStore(s, settings.data_dir, settings.config_dir).get(st["version_id"])
        actx = context_from(settings, s)
        cv = row.content
        from summer_hunter.cv_builder.intent import CVRequest
        req = CVRequest.from_dict(row.request)
    orgs = sorted(set(actx.companies) | set(actx.own_orgs))
    v = ae.voice(settings.config_dir)
    md = [f"# Email — {r['official_name']} ({st['tag']}, CCI27-{cid})", "", "Status: DRAFT — NOT SENT", ""]
    for lang in ["en"] + (["fr"] if c.get("email_fr") else []):
        src = EMAILS / f"{cid}.{lang}.txt"
        subject, body, paragraphs = parse_email(src)
        ctx = from_request(replace(req, language=lang), settings.profile_raw)
        _, _, school = _people(cv)
        signature = _signature(cv, school, lang == "fr")
        sign_off = "Bien cordialement," if lang == "fr" else "Best regards,"
        full = "\n\n".join([body, sign_off, signature])
        greeting, paras = paragraphs and body.split("\n\n")[0], paragraphs
        parts = {"subject": subject, "greeting": greeting, "opening": "", "closing": "", "proof": "\n\n".join(paras),
                 "slots": list(ctx.names), "paragraphs": paras}
        st_ = ae.build_strategy(cv, cv, ctx, "international", req.family, "spontaneous", settings.config_dir)
        cv_text = " ".join([str(cv.get("summary", ""))] + [b["text"] for it in cv.get("experiences", []) + cv.get("projects", [])
                                                           for b in it.get("bullets", [])])
        allowed = " ".join([cv_text, json.dumps(r, ensure_ascii=False), " ".join(st_.goals), ctx.availability, "3 5 2027"])
        quality = ae.check(paras, subject, st_, ctx, cv_text, allowed, str(v.get("reference_email", "")),
                           list(v.get("banned_phrases") or []), list(v.get("never_mention") or []),
                           [x for items in (cv.get("skills") or {}).values() for x in items], orgs, actx.own_orgs,
                           list(v.get("reference_phrases_not_to_reuse") or []))
        problems = validate_email(parts, ctx, orgs, actx.own_orgs)
        for other_id, names in others(cid).items():
            problems += [f"names another chamber ({other_id}: {n})" for n in names if mentions(full, n)]
        if problems or quality.blocking:
            raise SystemExit(f"{cid} {lang}: BLOCKED: {problems + quality.blocking}")
        service.set_custom_email(settings, sessions, st["version_id"], subject, full, lang, parts)
        words = len(re.findall(r"[A-Za-zÀ-ÿ0-9'’-]+", "\n\n".join(paras)))
        md += [f"## {'English' if lang == 'en' else 'Français'}", "", f"**{'Subject' if lang == 'en' else 'Objet'}:** {subject}", "",
               "```", full, "```", "", f"Words (without greeting and signature): {words} · checks: "
               + ("OK" if not quality.quality else "to reread: " + "; ".join(quality.quality)), ""]
        print(f"{cid} {lang}: OK · {words} words" + (f" · to reread: {quality.quality}" if quality.quality else ""))
    (folder / f"Email_{c['tag']}.md").write_text("\n".join(md), encoding="utf-8")


# Verification and summary ----------------------------------------------------------------------------

def cmd_verify() -> int:
    bad = 0
    for c in CFG["chambers"]:
        folder = OUT / c["folder"]
        if not folder.exists():
            print(f"{c['id']}: not generated yet")
            continue
        r = research(c["id"])
        ctx = json.loads((folder / "context.json").read_text())
        pdf, docx = folder / f"CV_Ilyess_Assadi_{c['tag']}.pdf", folder / f"CV_Ilyess_Assadi_{c['tag']}.docx"
        email = folder / f"Email_{c['tag']}.md"
        problems = []
        cv_text = pdf_text(pdf) if pdf.exists() else ""
        pages = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout if pdf.exists() else ""
        if not pdf.exists() or not docx.exists() or docx.read_bytes()[:2] != b"PK":
            problems.append("CV PDF/DOCX missing or invalid")
        if not re.search(r"^Pages:\s+1\s*$", pages, re.M):
            problems.append("CV is not one page")
        if not mentions(cv_text, r["official_name"]):
            problems.append("CV does not name the target chamber")
        if ctx["organisation"] != r["official_name"]:
            problems.append("context organisation differs from research")
        texts = {"CV": cv_text, "email": email.read_text() if email.exists() else ""}
        if not email.exists():
            problems.append("email not written yet")
        for other_id, names in others(c["id"]).items():
            for kind, text in texts.items():
                problems += [f"{kind} names chamber {other_id} ({n})" for n in names if mentions(text, n)]
        if "airbus" in normalize_for_match(texts["email"]) and "airbus helicopters" not in normalize_for_match(texts["email"]):
            problems.append("email names Airbus outside the Airbus Helicopters experience")
        bad += bool(problems)
        print(f"{c['id']} {c['folder']}: " + ("OK" if not problems else "PROBLEMS: " + "; ".join(problems)))
    return bad


def cmd_summary() -> None:
    rows = ["| # | CCI | CV | Email | Verified contact | Visa | Pay | Status |", "|---|---|---|---|---|---|---|---|"]
    for c in CFG["chambers"]:
        folder = OUT / c["folder"]
        if not (RESEARCH / f"{c['id']}.json").exists():
            continue
        r = research(c["id"])
        named = [x for x in r.get("contacts") or [] if x.get("name")]
        contact = f"{named[0]['name']} ({named[0]['role']})" if named else ("general: " + str((r.get("general_contact") or {}).get("email") or NOT))
        rows.append(f"| {c['id']} | {r['official_name']} | {'✅' if (folder / ('CV_Ilyess_Assadi_' + c['tag'] + '.pdf')).exists() else '—'} | "
                    f"{'✅' if (folder / ('Email_' + c['tag'] + '.md')).exists() else '—'} | {contact} | "
                    f"{((r.get('visa') or {}).get('info') or NOT)[:60]} | {((r.get('pay') or {}).get('info') or NOT)[:50]} | DRAFT — NOT SENT |")
    (OUT / "README.md").write_text("# CCI France International 2027 — campaign\n\nNothing has been sent. Every application is a "
                                   "draft to reread.\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
    zip_base = OUT.parent / CFG["campaign"]
    shutil.make_archive(str(zip_base), "zip", OUT.parent, OUT.name)
    print("\n".join(rows))
    print(f"ZIP: {zip_base}.zip")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["cv", "email", "verify", "summary"])
    ap.add_argument("--wave", type=int)
    ap.add_argument("--id")
    ap.add_argument("--research")
    a = ap.parse_args()
    if a.cmd == "cv":
        cmd_cv(a.wave, a.id)
    elif a.cmd == "email":
        cmd_email(a.id)
    elif a.cmd == "verify":
        sys.exit(1 if cmd_verify() else 0)
    else:
        cmd_summary()
