"""Evaluate the application email engine on the reference cases (A-J) and keep the drafts for human review.

    uv run python scripts/eval_emails.py [--provider ollama|anthropic] [--model NAME] [--out docs/email_examples/DATE]

For each case: the previous deterministic email ("before"), the new draft ("after"), the model's strategy answers,
objective metrics (words, clichés, CV overlap, checks left, attempts, seconds) and an empty rubric for a human:
argument, personalisation, natural writing, path coherence, closing, factual accuracy, CV/email coherence (1-5).
Nothing is sent and nothing is stored in the application database (a throwaway database is used).
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from summer_hunter.cv_builder import application_email as ae  # noqa: E402
from summer_hunter.cv_builder.context import from_request  # noqa: E402
from summer_hunter.cv_builder.document import compose as compose_cv  # noqa: E402
from summer_hunter.cv_builder.email import compose as old_compose  # noqa: E402
from summer_hunter.cv_builder.intent import analyze, context_from  # noqa: E402
from summer_hunter.database.migrations import init_db, make_engine  # noqa: E402
from summer_hunter.utils.settings import Settings  # noqa: E402
from summer_hunter.web.cv import load_content  # noqa: E402

FCCS = (ROOT / "tests" / "fixtures" / "fccs_request.txt").read_text(encoding="utf-8").strip()
CASES = {
    "A_fccs_singapore": FCCS,
    "B_cci_france_uae_dubai": "Candidature spontanée à la CCI France UAE (Dubai) pour un stage Business Development, avril à août 2027",
    "C_airbus_business_development": "Candidature spontanée chez Airbus pour un stage Business Development à Singapour",
    "D_robotics_startup": "Fais-moi un CV pour un stage Robotics Engineer chez une startup à Singapour.",
    "E_strategy_operations": "Candidature spontanée chez Capgemini Invent pour un stage Strategy & Operations à Paris",
    "F_published_offer": ("Business Development Intern\nCompany: Acme Robotics\nMunich, Germany\n\nAbout the role\nAcme builds "
                          "autonomous mobile robots for warehouses. You will prospect industrial customers in Europe, prepare "
                          "demos with the engineering team and follow partnership leads.\n\nRequirements\n- Engineering or "
                          "business studies\n- Fluent English\n- Interest in robotics and B2B sales"),
    "G_executive_contact": "Prise de contact avec le CEO de Mistral AI pour un stage AI Business Development à Paris",
    "H_company_change": "Candidature spontanée chez Google Cloud pour un stage Tech Sales à Dublin",
    "J_no_verified_info": "Candidature spontanée chez Orbital Freight Systems pour un stage Business Development à Rotterdam",
}
RUBRIC = ["Argument (relevance)", "Personalisation", "Natural writing", "Path coherence", "Closing", "Factual accuracy",
          "CV / email coherence"]


def metrics(body: str, cv_text: str, banned: list[str]) -> dict:
    words = len(re.findall(r"[A-Za-zÀ-ÿ0-9'’+#-]+", body))
    low = body.lower()
    grams = lambda t, n=7: {tuple(t[i:i + n]) for i in range(len(t) - n + 1)}  # noqa: E731
    w1, w2 = re.findall(r"[a-z0-9]+", low), re.findall(r"[a-z0-9]+", cv_text.lower())
    return {"words": words, "cliches": [b for b in banned if b in low], "cv_7gram_overlap": len(grams(w1) & grams(w2))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=str(ROOT / "docs" / "email_examples" / date.today().isoformat()))
    ap.add_argument("--only", default=None)
    args = ap.parse_args()
    if args.provider:
        os.environ["SH_EMAIL_PROVIDER"] = args.provider
    live = Settings.load()
    llm = dict(live.llm)
    if args.model:
        llm["emails"] = dict(llm.get("emails") or {}) | {"model": args.model}
    provider = ae.provider_from(llm, live.anthropic_api_key)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        sessions = init_db(make_engine(f"sqlite:///{tmp}/eval.db"))
        with sessions() as s:
            actx = context_from(live, s)
    content = load_content(live.config_dir)
    orgs = sorted(set(actx.companies) | set(actx.own_orgs))
    banned = list(ae.voice(live.config_dir).get("banned_phrases") or [])
    summary = ["| Case | Model | Seconds | Attempts | Words | Clichés | CV 7-gram overlap | Checks left |", "|---|---|---|---|---|---|---|---|"]
    openings: list[str] = []
    for name, text in CASES.items():
        if args.only and args.only not in name:
            continue
        req = analyze(text, actx)
        cv, _ = compose_cv(content, req, [])
        cv.pop("_content", None)
        ctx = from_request(req, live.profile_raw)
        before = old_compose(cv, ctx, req.track)
        t = time.time()
        after = ae.write(cv, content, ctx, req.track, req.family, req.situation, live.config_dir, live.profile_raw, orgs,
                         actx.own_orgs, provider, source_text=req.source_text if req.mode != "offer" else "",
                         posting=req.source_text if req.mode == "offer" else "", recent_openings=openings,
                         rng=random.Random(hash(name) % 1000))
        secs = time.time() - t
        paragraphs = after.parts.get("paragraphs") or []
        if paragraphs:
            openings.append(re.split(r"(?<=[.!?])\s+", paragraphs[0])[0][:160])
        cv_text = " ".join([str(cv.get("summary", ""))] + [b["text"] for it in cv.get("experiences", []) + cv.get("projects", [])
                                                           for b in it.get("bullets", [])])
        m_after, m_before = metrics(after.body, cv_text, banned), metrics(before.body, cv_text, banned)
        summary.append(f"| {name} | {after.model} | {secs:.0f} | {after.attempts} | {m_after['words']} | "
                       f"{len(m_after['cliches'])} | {m_after['cv_7gram_overlap']} | {len(after.warnings)} |")
        doc = [f"# {name}", "", f"Request: {text[:300]}{'…' if len(text) > 300 else ''}", "",
               f"Situation: {req.situation} · story line: {after.strategy.get('thread')} · organisation facts: "
               f"{'yes' if after.strategy.get('organisation_facts') else 'none (must not be described)'}", "",
               "## Strategy (model answers)", "", *[f"- **{k}**: {v}" for k, v in after.answers.items()], "",
               "## After", "", "```", after.text, "```", "",
               f"Model {after.model}, {secs:.0f} s, {after.attempts} attempt(s). Words {m_after['words']}, clichés "
               f"{m_after['cliches'] or 'none'}, CV 7-gram overlap {m_after['cv_7gram_overlap']}.",
               f"Checks left (warnings): {after.warnings or 'none'}", "",
               "## Before (previous deterministic engine)", "", "```", before.text, "```", "",
               f"Words {m_before['words']}, CV 7-gram overlap {m_before['cv_7gram_overlap']}.", "",
               "## Human review (1-5)", "", "| Criterion | Score | Note |", "|---|---|---|",
               *[f"| {c} |  |  |" for c in RUBRIC]]
        (out / f"{name}.md").write_text("\n".join(doc) + "\n", encoding="utf-8")
        print(summary[-1], flush=True)
    (out / "README.md").write_text("# Application email evaluation\n\n" + "\n".join(summary) + "\n\nOne file per case: "
                                   "before/after, strategy, metrics, rubric to fill.\n", encoding="utf-8")


if __name__ == "__main__":
    main()
