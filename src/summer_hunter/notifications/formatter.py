"""Telegram message formatting. All external content is HTML-escaped; only http(s) links become buttons."""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlsplit

from summer_hunter.database.models import Contact, Opportunity
from summer_hunter.engine.feedback import DISMISS_REASONS
from summer_hunter.engine.geo import country_name
from summer_hunter.utils.text import truncate

DECISION_LABEL = {"ELIGIBLE": "ELIGIBLE", "NEEDS_VERIFICATION": "NEEDS VERIFICATION", "REJECTED": "REJECTED"}
TIER_EMOJI = {"exceptional": "🚀", "high": "⭐", "review": "🔎", "suppressed": "·", "rejected": "⛔"}


def safe_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme in {"http", "https"} and parts.netloc:
        return url.strip()[:1000]
    return None


def e(text: Any, limit: int = 300) -> str:
    return escape(truncate(str(text) if text is not None else "UNKNOWN", limit), quote=False)


def _dates_label(criteria: list[dict]) -> str:
    crit = next((c for c in criteria if c["criterion"] == "dates"), None)
    if not crit:
        return "UNKNOWN"
    if crit["status"] == "PASS":
        return crit["explanation"].split(" fits")[0].replace("Stated period ", "")
    return f"{crit['status']}: {crit['explanation']}"


INDUSTRY_TRACK = "industry_robotics"
TRACK_LABEL = {"industry_robotics": "INDUSTRY & ROBOTICS", "business_tech": "BUSINESS & TECHNOLOGY"}


def card(opp: Opportunity, criteria: list[dict], fit: dict, networking: dict, priority: dict,
         contacts: list[Contact], webapp_url: str | None = None,
         recruit: dict | None = None, comp: dict | None = None,
         strategy: dict | None = None) -> tuple[str, list[list[dict[str, str]]]]:
    tier = priority.get("tier", "review")
    industrial = opp.career_track == INDUSTRY_TRACK
    head = "🚀 SUMMER 2027 — NEW OPPORTUNITY" if tier in {"exceptional", "high"} else "🔎 SUMMER 2027 — WORTH REVIEWING"
    if industrial:
        head = "🏭 INDUSTRIAL INTERNSHIP — SUMMER 2027"
    if priority.get("strategic_bypass"):
        head = "🎯 SUMMER 2027 — STRATEGIC OPPORTUNITY"
    location = opp.location_text or "UNKNOWN"
    if opp.country_code:
        location = f"{location} ({country_name(opp.country_code)})"
    unknowns = [c for c in criteria if c["status"] == "UNKNOWN" and c.get("material", True)]
    weak_fails = [c for c in criteria if c["status"] == "FAIL"]
    lines = [
        f"<b>{head}</b>",
        "",
        f"<b>Company:</b> {e(opp.company_name)}",
        f"<b>Role:</b> {e(opp.title)}",
        f"<b>Location:</b> {e(location)}",
        f"<b>Dates:</b> {e(_dates_label(criteria), 160)}",
        "",
        f"<b>Career Fit:</b> {fit.get('score')}/100" + (" (incl. AI analysis)" if fit.get("method") == "deterministic+llm" else ""),
        *([f"<b>Recruitability:</b> {recruit.get('score')}/100 <i>(match with your verified experience, not a probability)</i>"]
          if recruit else []),
        f"<b>Networking:</b> {networking.get('score')}/100",
        *compensation_lines(comp),
        f"<b>Eligibility:</b> {DECISION_LABEL.get(opp.eligibility_status or '', 'UNKNOWN')}",
        f"<b>Priority:</b> {priority.get('priority_score')} ({e(tier)})",
        *strategy_lines(opp, strategy),
        "",
        "<b>Why this is relevant:</b>" if industrial else "<b>Why this fits:</b>",
        *[f"• {e(r, 200)}" for r in _why(fit, recruit, industrial)],
    ]
    uncertainties = [f"{c['criterion'].replace('_', ' ')}: {c['explanation']}" for c in (weak_fails + unknowns)[:3]]
    if recruit:
        uncertainties = [*recruit.get("concerns", [])[:2], *uncertainties]
    uncertainties += [w for w in fit.get("weaknesses", [])[:2] if not w.startswith("Unverified")]
    if uncertainties:
        lines += ["", "<b>Main concerns:</b>" if industrial else "<b>Main uncertainties:</b>",
                  *[f"• {e(u, 200)}" for u in uncertainties[:4]]]
    if fit.get("injection_flags"):
        lines += ["", "⚠️ Listing contains instruction-like text; it was ignored."]
    best = max(contacts, key=lambda c: c.confidence, default=None)
    lines += ["", "<b>Contact:</b>"]
    if best:
        lines.append(f"{e(best.name, 80)}, {e(best.relationship_type.replace('_', ' '))} (confidence {best.confidence:.2f})")
    else:
        lines.append("No verified recruiter or hiring manager.")
    if opp.career_category:
        lines.append(f"<b>Category:</b> {e(TRACK_LABEL.get(opp.career_track or '', ''))} · {e(opp.career_category)}")
    lines.append(f"<i>#{opp.id}</i>")
    return "\n".join(lines), buttons(opp, best, webapp_url)


ACTION_LABEL = {"APPLY_NOW": "APPLY NOW", "PREPARE_APPLICATION": "PREPARE APPLICATION", "CONTACT_RECRUITER": "CONTACT RECRUITER",
                "VERIFY_ELIGIBILITY": "VERIFY ELIGIBILITY", "VERIFY_COMPENSATION": "VERIFY COMPENSATION",
                "MONITOR_OPENING": "MONITOR", "LOW_PRIORITY": "LOW PRIORITY", "DISCARD": "DISCARD"}


def compensation_lines(comp: dict | None) -> list[str]:
    """Pay as advertised, its monthly EUR equivalent, living costs and balance only when sourced."""
    if not comp:
        return []
    status = comp.get("status", "COMPENSATION_UNKNOWN")
    if comp.get("monthly_min_eur") is not None:
        lo, hi = comp["monthly_min_eur"], comp.get("monthly_max_eur")
        amount = f"€{lo:,.0f}" + (f"-{hi:,.0f}" if hi and hi != lo else "") + "/month"
        pay = comp.get("pay") or {}
        note = " (estimate)" if comp.get("monthly_is_estimate") else ""
        basis = "" if pay.get("basis") in (None, "unknown") else f", {pay['basis']}"
        out = [f"<b>Compensation:</b> {e(amount)}{note}{e(basis)}"]
    else:
        out = [f"<b>Compensation:</b> {e({'UNPAID_CONFIRMED': 'unpaid (stated)', 'PAID_AMOUNT_UNKNOWN': 'paid, amount not stated', 'MIXED_OR_CONDITIONAL': 'conditional'}.get(status, 'not stated'))}"]
    if comp.get("living_cost_eur") is not None:
        out.append(f"<b>Estimated Living Costs:</b> €{comp['living_cost_eur']:,.0f}/month")
    if comp.get("monthly_balance_eur") is not None:
        out.append(f"<b>Estimated Monthly Balance:</b> {comp['monthly_balance_eur']:+,.0f} €")
    out.append(f"<b>Compensation Confidence:</b> {e(comp.get('confidence', 'UNKNOWN'))}")
    return out


def strategy_lines(opp: Opportunity, strategy: dict | None) -> list[str]:
    if not strategy or opp.global_priority is None and not strategy.get("action"):
        return []
    gp = "n/a" if opp.global_priority is None else f"{opp.global_priority:.0f}/100" + (" (provisional)" if opp.global_provisional else "")
    rec = ACTION_LABEL.get(strategy.get("action", ""), strategy.get("action", ""))
    if strategy.get("caveat"):
        rec += f", {strategy['caveat']}"
    return [f"<b>Global Priority:</b> {e(gp)}", f"<b>Recommendation:</b> {e(rec, 200)}"
            + (f" · {e(strategy.get('category', '').lower())}" if strategy.get("category") else "")]


def _why(fit: dict, recruit: dict | None, industrial: bool) -> list[str]:
    """Industrial cards lead with the experience evidence; business cards keep the career-fit reasons."""
    if industrial and recruit:
        merged = [*recruit.get("reasons", []), *fit.get("reasons", [])]
        return list(dict.fromkeys(merged))[:3]
    return fit.get("reasons", [])[:3]


def buttons(opp: Opportunity, contact: Contact | None, webapp_url: str | None = None) -> list[list[dict[str, str]]]:
    row1 = []
    apply = safe_url(opp.apply_url) or safe_url(opp.official_url)
    if apply:
        row1.append({"text": "Apply", "url": apply})
    post = safe_url(opp.linkedin_post_url)
    if post:
        row1.append({"text": "LinkedIn Post", "url": post})
    contact_url = safe_url(contact.profile_url or contact.source_post_url) if contact else None
    if contact_url:
        row1.append({"text": "Contact", "url": contact_url})
    row2 = [{"text": "Save", "callback_data": f"sv:{opp.id}"}, {"text": "Track", "callback_data": f"tr:{opp.id}"},
            {"text": "Applied", "callback_data": f"ap:{opp.id}"}, {"text": "Dismiss", "callback_data": f"dm:{opp.id}"}]
    row3 = [{"text": "🧾 Generate application", "callback_data": f"ga:{opp.id}"},
            {"text": "💶 Compensation", "callback_data": f"vc:{opp.id}"}]
    row4 = [{"text": "✅ Verify eligibility", "callback_data": f"ve:{opp.id}"},
            {"text": "🤝 Contact recruiter", "callback_data": f"nm:{opp.id}"}]
    row5 = [{"text": "📄 CV", "callback_data": f"cv:{opp.id}"}, {"text": "✉️ Lettre", "callback_data": f"lm:{opp.id}"},
            {"text": "Explain Score", "callback_data": f"ex:{opp.id}"}, {"text": "⭐ Rate", "callback_data": f"rt:{opp.id}"}]
    row0 = [{"text": "📱 Ouvrir · CV adapté", "web_app": {"url": f"{webapp_url}/#/o/{opp.id}"}}] if safe_url(webapp_url) else []
    return [r for r in (row0, row1, row2, row3, row4, row5) if r]


def dismiss_reason_buttons(opp_id: int) -> list[list[dict[str, str]]]:
    return [[{"text": label, "callback_data": f"rs:{opp_id}:{code}"}] for code, (_, _, label) in DISMISS_REASONS.items()]


def explain(opp: Opportunity, criteria: list[dict], fit: dict, networking: dict, priority: dict, sponsorship: str,
            recruit: dict | None = None) -> str:
    comps = fit.get("components", {})
    lines = [f"<b>Score breakdown #{opp.id}</b>: {e(opp.title, 120)} @ {e(opp.company_name, 80)}", "",
             f"<b>Career fit {fit.get('score')}/100</b> (method: {e(fit.get('method'))})"]
    lines += [f"• {k.replace('_', ' ')}: {v:.0f}" for k, v in comps.items()]
    for a in fit.get("adjustments", []):
        lines.append(f"• adjustment: {e(a['label'], 120)} ({a['delta']:+g}{' on ' + a['component'] if a.get('component') else ''})")
    if fit.get("llm_score") is not None:
        lines.append(f"• AI assessment {fit['llm_score']} vs deterministic {fit.get('deterministic_score')} (blended)")
    lines += ["", "<b>Relevant experience:</b> " + e(", ".join(fit.get("relevant_experiences", [])) or "none matched"),
              "<b>Missing skills:</b> " + e(", ".join(fit.get("missing_skills", [])) or "none detected"),
              "<b>Career value:</b> " + e(fit.get("career_value", ""))]
    if fit.get("evidence"):
        lines += ["", "<b>Evidence:</b>", *[f"• “{e(x, 180)}”" for x in fit["evidence"][:3]]]
    lines += ["", f"<b>Eligibility: {DECISION_LABEL.get(opp.eligibility_status or '', 'UNKNOWN')}</b> (sponsorship: {e(sponsorship.replace('_', ' '))})"]
    icon = {"PASS": "✅", "FAIL": "❌", "UNKNOWN": "❔", "NOT_APPLICABLE": "➖"}
    for c in criteria:
        lines.append(f"{icon.get(c['status'], '?')} {e(c['criterion'].replace('_', ' '))}: {e(c['explanation'], 220)}")
    if recruit:
        lines += ["", f"<b>Recruitability {recruit.get('score')}/100</b> <i>(match with verified experience, not a probability)</i>"]
        lines += [f"• {k.replace('_', ' ')}: {v:.0f}" for k, v in (recruit.get("components") or {}).items()]
        if recruit.get("relevant_experience"):
            lines.append("<b>Relevant experience:</b> " + e("; ".join(recruit["relevant_experience"]), 300))
        if recruit.get("missing_requirements"):
            lines.append("<b>Missing technical requirements:</b> " + e(", ".join(recruit["missing_requirements"]), 300))
        if not recruit.get("technical_requirements_known", True):
            lines.append("<i>Technical requirements are not stated clearly: skill match unknown.</i>")
    lines += ["", f"<b>Networking {networking.get('score')}/100:</b> {e(networking.get('rationale'), 300)}",
              f"<b>Priority:</b> {priority.get('priority_score')} ({e(priority.get('reason'), 200)})"]
    return _join_within(lines, 3900)


def _join_within(lines: list[str], limit: int) -> str:
    """Join whole lines only, so a long message is never cut inside an HTML tag."""
    out, size = [], 0
    for line in lines:
        if size + len(line) + 1 > limit:
            out.append("…")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def digest(stats: dict[str, int], best: list[tuple[Opportunity, dict]], leads: list[Any], source_issues: list[str],
           window_label: str, crm_lines: list[str] | None = None, crm_actions: list[str] | None = None,
           headline: list[str] | None = None, sections: list[tuple[str, list[str]]] | None = None) -> str:
    """Daily briefing: new opportunities, then the application CRM and the actions due (all from stored records)."""
    lines = [
        "<b>☀️ SUMMER HUNTER 2027 — DAILY BRIEFING</b>",
        f"<i>{e(window_label)}</i>",
        "",
        *([e(h, 200) for h in headline] + [""] if headline else []),
        "<b>NEW OPPORTUNITIES</b>",
        f"• {stats.get('shortlisted', 0)} high-fit internship(s) shortlisted"
        + (f", {stats['with_contacts']} with a direct recruiter or hiring-manager contact" if stats.get("with_contacts") else "")
        + (f", {stats['exceptional']} exceptional" if stats.get("exceptional") else ""),
        f"Discovered: <b>{stats.get('discovered', 0)}</b>",
        f"Rejected: {stats.get('rejected', 0)}",
        f"Needs verification: {stats.get('needs_verification', 0)}",
        f"Eligible (all checks passed): {stats.get('eligible', 0)}",
        f"Shortlisted: <b>{stats.get('shortlisted', 0)}</b>",
    ]
    if crm_lines is not None:   # before the long opportunity list, so truncation can never cut the actions
        lines += ["", "<b>APPLICATION CRM</b>", *crm_lines]
        lines += ["", "<b>ACTIONS REQUIRED</b>", *([f"• {a}" for a in crm_actions] if crm_actions else ["• Rien à faire aujourd'hui."])]
    for title, rows in sections or []:     # V3 sections: already escaped lines built from stored records
        lines += ["", f"<b>{e(title)}</b>", *([f"• {r}" for r in rows] if rows else ["• None."])]
    if best:
        lines += ["", "<b>Best new opportunities:</b>"]
        for opp, fit in best[:25]:
            lines.append(f"{TIER_EMOJI.get(opp.priority_tier or '', '•')} {e(opp.title, 80)} @ {e(opp.company_name, 40)}, "
                         f"{e(country_name(opp.country_code), 30)} · fit {fit.get('score')} · {DECISION_LABEL.get(opp.eligibility_status or '', '?')} #{opp.id}")
    else:
        lines += ["", "No new opportunity above the threshold tonight."]
    if leads:
        lines += ["", "<b>LinkedIn leads to check (no company or role extracted):</b>"]
        for post in leads[:5]:
            url = safe_url(post.post_url)
            label = e(post.author_name or "LinkedIn post", 60)
            lines.append(f"• <a href=\"{escape(url, quote=True)}\">{label}</a>: {e(post.classification.replace('_', ' '))}" if url else f"• {label}")
    if source_issues:
        lines += ["", "<b>Source issues:</b>", *[f"• {e(s, 160)}" for s in source_issues[:5]]]
    return _join_within(lines, 4000)
