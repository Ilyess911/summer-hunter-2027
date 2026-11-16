"""Skills ready to paste into an application form's "Skills" field, chosen per offer.

Only skills the candidate holds are listed: the approved CV's skill groups (every track) plus profile facts
the candidate confirmed. Skills held at a limited level ("Academic Coursework", "Beginner") and languages
are left out: a bare tag in a form drops the qualifier. Offer-matched skills come first, then the
tailored CV's own skills for that offer's track, so the list never pads with unrelated skills.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from summer_hunter.utils.text import normalize_for_match

MAX_SKILLS = 15
MAX_CHARS = 256            # Telegram copy_text limit; the shown list is the copied list
LIMITED = re.compile(r"(?i)academic|beginner|coursework")
# Short names the offer writes differently from the CV.
ALIASES = {
    "microsoft excel": [r"\bexcel\b"], "power query": [r"power query", r"power bi"],
    "kpi monitoring": [r"\bkpis?\b", r"performance indicators?"], "crm": [r"\bcrm\b", r"salesforce", r"hubspot"],
    "lean management (yellow belt)": [r"\blean\b"], "continuous improvement": [r"continuous improvement", r"kaizen"],
    "process improvement": [r"process (improvement|optimi[sz]ation)"], "process automation": [r"automat\w*"],
    "data analysis": [r"data analy\w*", r"analy[sz]e data", r"analytics"], "ai tools": [r"\bai\b", r"artificial intelligence", r"genai|llm"],
    "machine learning": [r"machine learning", r"\bml\b"], "saas": [r"\bsaas\b"], "mro": [r"\bmro\b", r"maintenance, repair"],
    "lead generation": [r"lead gen\w*", r"prospect\w*"], "business development": [r"business development", r"\bbizdev\b", r"new business"],
    "stakeholder engagement": [r"stakeholders?"], "stakeholder communication": [r"stakeholders?", r"cross-functional"],
    "technical project management": [r"project management"], "project coordination": [r"project coordination", r"coordinat\w* projects?"],
    "matlab/simulink": [r"\bmatlab\b", r"simulink"], "plm": [r"\bplm\b", r"product lifecycle"], "git": [r"\bgit\b", r"github|gitlab"],
    "c++": [r"c\+\+"], "industrial automation": [r"industrial automation", r"\bautomation\b"],
    "public speaking": [r"public speaking", r"presentations?"], "consultative selling": [r"consultative", r"solution selling"],
    "customer outreach": [r"outreach"], "partnership outreach": [r"partnerships?"], "inventory management": [r"inventory|stock management"],
    "cost optimization": [r"cost (reduction|optimi[sz]ation|saving)"], "3d printing": [r"3d print\w*", r"additive manufacturing"],
}


def _norm(s: str) -> str:
    return normalize_for_match(s).strip()


def _pool(content: dict[str, Any], track: str, confirmed: list[str]) -> tuple[list[str], set[str]]:
    """Every held skill (tailored track first) and the names of the track's own skills."""
    fixed = set(content.get("fixed_order_skills", ["Languages"]))
    tracks = content.get("tracks") or {}
    own = (tracks.get(track) or {}).get("skills") or content.get("skills") or {}
    groups = [own, content.get("skills") or {}, *[(t.get("skills") or {}) for t in tracks.values()]]
    out, seen = [], set()
    for g in groups:
        for name, items in g.items():
            if name in fixed:
                continue
            for s in items:
                if isinstance(s, str) and not LIMITED.search(s) and _norm(s) not in seen:
                    seen.add(_norm(s))
                    out.append(s)
    for s in confirmed:
        if not LIMITED.search(s) and _norm(s) not in seen:
            seen.add(_norm(s))
            out.append(s)
    track_names = {_norm(s) for name, items in own.items() if name not in fixed for s in items if isinstance(s, str)}
    return out, track_names


def _score(skill: str, offer: str) -> int:
    n = _norm(skill)
    if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", offer):
        return 3
    if any(re.search(rx, offer) for rx in ALIASES.get(n, [])):
        return 2
    words = [w for w in re.findall(r"[a-z][a-z+#-]{3,}", n) if w not in {"management", "monitoring", "tools"}]
    return 1 if words and all(re.search(rf"\b{re.escape(w)}", offer) for w in words) else 0


def select(content: dict[str, Any], confirmed: list[str], title: str, text: str, family: str | None) -> list[str]:
    from summer_hunter.web.cv import role_text, tailor

    tailored = tailor(content, title, text or "", family)
    track = tailored.get("track", "")
    pool, track_names = _pool(content, track, confirmed)
    offer = _norm(f"{title} {role_text(text or '')}")
    scored = [(_score(s, offer), i, s) for i, s in enumerate(pool)]
    matched = [s for sc, _, s in sorted(scored, key=lambda x: (-x[0], x[1])) if sc > 0]
    tailored_order = [s for items in (tailored.get("skills") or {}).values() for s in items]
    filler = [s for s in tailored_order if _norm(s) in track_names and not LIMITED.search(s) and s not in matched]
    out: list[str] = []
    for s in [*matched, *filler]:
        if len(out) >= MAX_SKILLS or len(", ".join([*out, s])) > MAX_CHARS:
            break
        out.append(s)
    return out


def for_opportunity(config_dir: Path, session, opp) -> list[str]:  # noqa: ANN001
    """Skills for one stored offer; empty when the CV content is missing (never blocks a card)."""
    from summer_hunter.cv_builder.profile import ProfileStore
    from summer_hunter.web.cv import load_content

    try:
        content = load_content(config_dir)
        confirmed = [f.text for f in ProfileStore(session).confirmed("skill")]
    except Exception:  # noqa: BLE001
        return []
    return select(content, confirmed, opp.title or "", opp.description_text or "", opp.job_family)


def as_text(skills: list[str]) -> str:
    return ", ".join(skills)
