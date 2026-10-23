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
    "maintenance_reliability": "maintenance equipment data analysis process improvement kpi reliability operations aircraft",
    "industrial_project_management": "project coordination planning stakeholder industrial process improvement reporting",
    "industrial_operations": "process improvement operations logistics kpi lean industrial cost optimization",
    "industrial_digital": "automation data python dashboards ai industrial digital decision",
    "robotics_raas": "deployment customer coordination automation robotics operations stakeholder solution",
    "robotics_specialized": "robotics automation engineering data",
}


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_YEAR = re.compile(r"\b([A-Za-z]{3})[a-z]*\.?\s+((?:19|20)\d\d)\b")
_RANGE_SEP = re.compile(r"\s*(?:--|–|—|-|\bto\b)\s*")


def ats_dates(text: Any) -> str:
    """Dates in the form resume parsers read reliably: "Sep 2024 -- Aug 2027" -> "09/2024 - 08/2027".

    ATS "apply with resume" parsers often mis-read an en dash between two month names and copy the
    start date as the end date ("2024 to 2024"). Numeric months and a plain hyphen avoid it.
    """
    value = str(text or "")
    value = _MONTH_YEAR.sub(lambda m: f"{_MONTHS[m.group(1).lower()]:02d}/{m.group(2)}" if m.group(1).lower() in _MONTHS
                            else m.group(0), value)
    return _RANGE_SEP.sub(" - ", value).strip()


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
    fixed = set(content.get("fixed_order_skills", []))
    for group, items in (content.get("skills") or {}).items():
        skills[group] = list(items) if group in fixed else sorted(items, key=lambda s: -len(_tokens(s) & keys))
    out["skills"] = skills
    return out


PREAMBLE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{geometry}
\usepackage{xcolor}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage{hyperref}
\usepackage{tabularx}
\usepackage{microtype}
\usepackage{ragged2e}
\geometry{top=1.25cm, bottom=1.25cm, left=1.58cm, right=1.58cm}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\linespread{1.055}
\definecolor{navy}{HTML}{17324D}
\definecolor{darkgray}{HTML}{333333}
\definecolor{lightgray}{HTML}{666666}
\hypersetup{colorlinks=true, urlcolor=navy, linkcolor=navy}
\titleformat{\section}{\color{navy}\bfseries\fontsize{10.5}{12}\selectfont}{}{0pt}{}[\vspace{-3pt}\color{navy}\titlerule]
\titlespacing*{\section}{0pt}{8pt}{5pt}
\setlist[itemize]{leftmargin=1.3em, topsep=2pt, itemsep=1.5pt, parsep=0pt, partopsep=0pt}
\newcommand{\cvrole}[4]{\textbf{#1} \hfill {\color{lightgray}\small #2}\\ \textit{#3} \hfill {\color{lightgray}\small #4}\vspace{-3pt}}
\newcommand{\cvedu}[3]{\textbf{#1} \hfill {\color{lightgray}\small #2}\\ #3}
"""


def _href(url: str, label: str) -> str:
    safe_url = re.sub(r"[^A-Za-z0-9:/._~?=&@+-]", "", url or "").replace("%", "").replace("#", "")
    return f"\\href{{{safe_url}}}{{{tex(label)}}}"


def _itemize(bullets: list[dict]) -> str:
    if not bullets:
        return ""
    return "\\begin{itemize}\n" + "".join(f"    \\item {tex(b['text'])}\n" for b in bullets) + "\\end{itemize}\n"


def render(cv: dict[str, Any], target: str) -> str:
    h = cv.get("header", {})
    labels = cv.get("labels", {})
    out = [PREAMBLE, f"% Tailored for: {tex(target)}\n", "\\begin{document}\n\\begin{center}\n",
           f"{{\\fontsize{{19}}{{21}}\\selectfont\\bfseries\\color{{navy}} {tex(h.get('name', ''))}}}\n\n\\vspace{{3pt}}\n\n"]
    if cv.get("headline"):
        out.append(f"{{\\fontsize{{10}}{{12}}\\selectfont\\textbf{{{tex(cv['headline'])}}}}}\n\n\\vspace{{4pt}}\n\n")
    contact = []
    if h.get("email"):
        contact.append(_href(f"mailto:{h['email']}", h["email"]))
    if h.get("phone"):
        contact.append(tex(h["phone"]))
    if contact:
        out.append(" \\quad | \\quad ".join(contact) + "\n\n\\vspace{2pt}\n\n")
    links = [_href(l["url"], l["label"]) for l in h.get("links", []) if l.get("url")]
    if links:
        out.append(" \\quad | \\quad ".join(links) + "\n\n\\vspace{3pt}\n\n")
    if h.get("availability"):
        parts = [f"\\textbf{{{tex(p.strip())}}}" for p in h["availability"].split("|")]
        out.append("{\\small " + " \\quad | \\quad ".join(parts) + "}\n")
    out.append("\\end{center}\n\\vspace{-5pt}\n")
    if cv.get("summary"):
        out.append(f"\\section*{{{tex(labels.get('summary', 'PROFILE'))}}}\n{tex(cv['summary'])}\n")
    out.append(f"\\section*{{{tex(labels.get('experience', 'PROFESSIONAL EXPERIENCE'))}}}\n")
    for i, exp in enumerate(cv.get("experiences", [])):
        if i:
            out.append("\\vspace{2pt}\n")
        out.append(f"\\cvrole{{{tex(exp.get('org'))}}}{{{tex(ats_dates(exp.get('dates')))}}}{{{tex(exp.get('role'))}}}{{{tex(exp.get('location'))}}}\n")
        out.append(_itemize(exp.get("bullets", [])))
    if cv.get("projects"):
        out.append(f"\\section*{{{tex(labels.get('projects', 'LEADERSHIP & ENTREPRENEURSHIP'))}}}\n")
        for i, pr in enumerate(cv["projects"]):
            if i:
                out.append("\\vspace{2pt}\n")
            out.append(f"\\textbf{{{tex(pr.get('name'))}}}\n" + _itemize(pr.get("bullets", [])))
    if cv.get("education"):
        out.append(f"\\section*{{{tex(labels.get('education', 'EDUCATION'))}}}\n")
        for i, ed in enumerate(cv["education"]):
            if i:
                out.append("\\vspace{3pt}\n\n")
            out.append(f"\\cvedu{{{tex(ed.get('school'))}}}{{{tex(ats_dates(ed.get('dates')))}}}{{{tex(ed.get('degree'))}}}\n\n")
    if cv.get("skills"):
        out.append(f"\\section*{{{tex(labels.get('skills', 'SKILLS & LANGUAGES'))}}}\n")
        groups = list(cv["skills"].items())
        for i, (group, items) in enumerate(groups):
            out.append(f"\\textbf{{{tex(group)}:}} {tex(', '.join(items))}\n\n")
            if i < len(groups) - 1:
                out.append("\\vspace{2pt}\n\n")
    out.append("\\end{document}\n")
    return "".join(out)


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
    for old in list(out_dir.glob("*.tex")) + list(out_dir.glob("*.pdf")):
        old.unlink()   # one current version per offer
    # Compile under a plain ASCII name (pdflatex dislikes spaces/accents), then give the files their final name.
    work = out_dir / "cv_build.tex"
    work.write_text(render(cv, f"{title} @ {company}"), encoding="utf-8")
    pdf, log = compile_pdf(work)
    final = document_name("CV", content.get("header", {}).get("name", ""), company, title)
    tex_path = work.rename(out_dir / f"{final}.tex")
    pdf_path = pdf.rename(out_dir / f"{final}.pdf") if pdf else None
    return CVResult(tex_path, pdf_path, log)


_ROLE_NOISE = re.compile(
    r"\((?:x|m|f|h|w|d)(?:/(?:x|m|f|h|w|d))+\)|\b(?:summer|spring|fall|autumn|winter|off[- ]cycle)\b|\b20[2-3]\d\b|"
    r"\[[^\]]*\]|\((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* intake\)", re.I)


def initial_name(full_name: str) -> str:
    """'ILYESS ASSADI' -> 'I.ASSADI'."""
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    if len(parts) < 2:
        return full_name.strip().upper()
    return f"{parts[0][0].upper()}.{' '.join(parts[1:]).upper()}"


def clean_role(title: str) -> str:
    role = _ROLE_NOISE.sub(" ", title)
    role = re.sub(r"\s*[-|,:]\s*(?=[-|,:]|$)", " ", role)       # dangling separators
    role = re.sub(r"\(\s*\)", " ", role)                          # emptied brackets
    lead = re.match(r"^\s*(intern(?:ship)?)\s*[-|,:]\s*(.+)$", role, flags=re.I)
    if lead:                                                         # 'Intern - Product GTM' -> 'Product GTM Intern'
        role = f"{lead.group(2)} {lead.group(1).title()}"
    role = re.sub(r"\s*\|\s*", " ", role)
    role = re.sub(r"\s{2,}", " ", role).strip(" -|,:()")
    return role or title.strip()


def document_name(kind: str, full_name: str, company: str, title: str) -> str:
    """e.g. 'CV I.ASSADI - Databricks - Product Management Intern'. Filesystem- and Telegram-safe."""
    name = f"{kind} {initial_name(full_name)} - {company.strip()} - {clean_role(title)}"
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", name)
    return re.sub(r"\s{2,}", " ", name).strip()[:120]
