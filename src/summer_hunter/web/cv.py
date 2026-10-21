"""Tailored LaTeX CV per opportunity.

Content comes only from `config/cv_content.yaml` (the candidate's own, verified CV text).
Tailoring never writes new claims: it picks the headline variant for the job family, orders
bullets and projects by relevance to the listing, and puts matching skills first.
All text is LaTeX-escaped, so nothing from a listing can inject LaTeX commands, and the
compiler runs with shell escape disabled.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from summer_hunter.utils.settings import load_yaml
from summer_hunter.utils.text import normalize_for_match

_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}

FAMILY_KEYWORDS = {
    "technical_sales": "customer demo discovery solution technical presales enterprise stakeholder value architecture",
    "enterprise_sales": "customer account enterprise stakeholder negotiation relationship value pipeline",
    "product_marketing": "market positioning messaging launch customer product go-to-market presentation",
    "go_to_market": "market partnership strategy launch customer business development outreach",
    "sales_general": "customer sales relationship outreach stakeholder",
    "sales_development": "outreach prospect customer pipeline",
    "product_management": "product prototype user data roadmap stakeholder ai",
    "ai_solutions": "ai data prototype automation customer solution deployment",
    "program_management": "project coordination stakeholder delivery planning",
    "commercial_strategy": "data analysis strategy cost optimization process operations",
    "strategy_consulting": "analysis strategy process improvement data stakeholder",
}


def tex(text: Any) -> str:
    out = "".join(_LATEX_SPECIAL.get(ch, ch) for ch in str(text or ""))
    return re.sub(r"\s+", " ", out).strip()


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z+#.-]{2,}", normalize_for_match(text))}


@dataclass
class CVResult:
    tex_path: Path
    pdf_path: Path | None
    log: str


def load_content(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "cv_content.yaml"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing")
    return load_yaml(path)


def _score(text: str, tags: list[str], keys: set[str]) -> int:
    words = _tokens(text) | {t.lower() for t in tags}
    return len(words & keys)


def tailor(content: dict[str, Any], job_title: str, job_text: str, job_family: str | None) -> dict[str, Any]:
    keys = _tokens(job_title + " " + job_text) | set(FAMILY_KEYWORDS.get(job_family or "", "").split())
    headlines = content.get("headlines", {})
    out = dict(content)
    out["headline"] = headlines.get(job_family or "", headlines.get("default", ""))
    exps = []
    for exp in content.get("experiences", []):
        bullets = sorted(exp.get("bullets", []), key=lambda b: -_score(b["text"], b.get("tags", []), keys))
        limit = int(exp.get("max_bullets", 3))
        exps.append(exp | {"bullets": bullets[:limit]})
    out["experiences"] = exps          # chronological order kept, bullets ranked
    out["projects"] = sorted(content.get("projects", []),
                             key=lambda p: -_score(" ".join(b["text"] for b in p.get("bullets", [])) + p.get("name", ""),
                                                   p.get("tags", []), keys))[: int(content.get("max_projects", 3))]
    skills = {}
    for group, items in (content.get("skills") or {}).items():
        skills[group] = sorted(items, key=lambda s: -len(_tokens(s) & keys))
    out["skills"] = skills
    return out


def render(cv: dict[str, Any], target: str) -> str:
    h = cv.get("header", {})
    contact = " \\textbar{} ".join(tex(x) for x in (h.get("location"), h.get("email"), h.get("phone"), h.get("linkedin")) if x)
    parts = [r"""\documentclass[10pt,a4paper]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{lmodern}
\usepackage[margin=1.5cm]{geometry}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage[hidelinks]{hyperref}
\usepackage{xcolor}
\definecolor{accent}{HTML}{1F3A5F}
\titleformat{\section}{\large\bfseries\color{accent}}{}{0em}{}[\titlerule]
\titlespacing*{\section}{0pt}{8pt}{4pt}
\setlist[itemize]{leftmargin=1.2em,itemsep=1pt,topsep=2pt}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\begin{document}
""",
             f"{{\\LARGE\\bfseries {tex(h.get('name', 'Prénom Nom'))}}}\\\\[2pt]\n",
             f"{{\\color{{accent}}{tex(cv.get('headline', ''))}}}\\\\[2pt]\n" if cv.get("headline") else "",
             f"{{\\small {contact}}}\n" if contact else "",
             f"% Tailored for: {tex(target)}\n"]
    if cv.get("summary"):
        parts.append(f"\\section*{{{tex(cv.get('labels', {}).get('summary', 'Profile'))}}}\n{tex(cv['summary'])}\n")
    labels = cv.get("labels", {})
    parts.append(f"\\section*{{{tex(labels.get('education', 'Education'))}}}\n")
    for ed in cv.get("education", []):
        where = (r" \hfill " + tex(ed.get("location"))) if ed.get("location") else ""
        parts.append(f"\\textbf{{{tex(ed.get('school'))}}} \\hfill {tex(ed.get('dates'))}\\\\\n{tex(ed.get('degree'))}{where}\n")
        if ed.get("details"):
            parts.append("\\begin{itemize}\n" + "".join(f"\\item {tex(d)}\n" for d in ed["details"]) + "\\end{itemize}\n")
        parts.append("\\smallskip\n")
    parts.append(f"\\section*{{{tex(labels.get('experience', 'Experience'))}}}\n")
    for exp in cv.get("experiences", []):
        parts.append(f"\\textbf{{{tex(exp.get('role'))}}}, {tex(exp.get('org'))} \\hfill {tex(exp.get('dates'))}\n")
        if exp.get("bullets"):
            parts.append("\\begin{itemize}\n" + "".join(f"\\item {tex(b['text'])}\n" for b in exp["bullets"]) + "\\end{itemize}\n")
    if cv.get("projects"):
        parts.append(f"\\section*{{{tex(labels.get('projects', 'Projects and leadership'))}}}\n")
        for pr in cv["projects"]:
            parts.append(f"\\textbf{{{tex(pr.get('name'))}}} \\hfill {tex(pr.get('dates'))}\n")
            if pr.get("bullets"):
                parts.append("\\begin{itemize}\n" + "".join(f"\\item {tex(b['text'])}\n" for b in pr["bullets"]) + "\\end{itemize}\n")
    if cv.get("skills"):
        parts.append(f"\\section*{{{tex(labels.get('skills', 'Skills'))}}}\n")
        for group, items in cv["skills"].items():
            parts.append(f"\\textbf{{{tex(group)}:}} {tex(', '.join(items))}\\\\\n")
    if cv.get("achievements"):
        parts.append(f"\\section*{{{tex(labels.get('achievements', 'Awards'))}}}\n\\begin{{itemize}}\n"
                     + "".join(f"\\item {tex(a)}\n" for a in cv["achievements"]) + "\\end{itemize}\n")
    parts.append("\\end{document}\n")
    return "".join(parts)


def _pdflatex() -> str | None:
    found = shutil.which("pdflatex")
    if found:
        return found
    for candidate in Path.home().glob("Library/TinyTeX/bin/*/pdflatex"):
        return str(candidate)
    return None


def compile_pdf(tex_path: Path, timeout: int = 90) -> tuple[Path | None, str]:
    exe = _pdflatex()
    if not exe:
        return None, "pdflatex not found (install TinyTeX); the .tex file is still available"
    cmd = [exe, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
    env = dict(os.environ, openout_any="p", openin_any="p")
    try:
        proc = subprocess.run(cmd, cwd=tex_path.parent, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, "pdflatex timed out"
    pdf = tex_path.with_suffix(".pdf")
    if proc.returncode != 0 or not pdf.exists():
        tail = "\n".join(proc.stdout.splitlines()[-15:])
        return None, f"pdflatex failed:\n{tail}"
    for ext in (".aux", ".log", ".out"):
        aux = tex_path.with_suffix(ext)
        if aux.exists():
            aux.unlink()
    return pdf, "ok"


def build_cv(config_dir: Path, out_dir: Path, opp_id: int, company: str, title: str, description: str,
             job_family: str | None) -> CVResult:
    content = load_content(config_dir)
    cv = tailor(content, title, description, job_family)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{company}_{title}")[:60].strip("_")
    tex_path = out_dir / f"CV_{opp_id}_{safe}.tex"
    tex_path.write_text(render(cv, f"{title} @ {company}"), encoding="utf-8")
    pdf, log = compile_pdf(tex_path)
    return CVResult(tex_path, pdf, log)
