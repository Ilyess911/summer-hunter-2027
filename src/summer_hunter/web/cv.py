"""Tailored LaTeX CV per opportunity.

Content comes only from `config/cv_content.yaml` (the candidate's own, verified CV text).
Tailoring never writes new claims: it picks the track (business or industry) and headline for the
job family, orders verified bullets and projects by relevance to the listing, puts matching skills
first, and reports what the offer asks for that the CV cannot show (gaps), without adding it.
All text is LaTeX-escaped, so nothing from a listing can inject LaTeX commands, and the
compiler runs with shell escape disabled.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
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


_SHORT_TERMS = {"ai", "ml", "5s", "3d", "bi"}   # meaningful acronyms below the 3-letter floor


def _tokens(text: str) -> set[str]:
    norm = normalize_for_match(text)
    return {w for w in re.findall(r"[a-z][a-z+#.-]{2,}", norm)} | (set(re.findall(r"\b[a-z0-9]{2}\b", norm)) & _SHORT_TERMS)


@dataclass
class CVResult:
    tex_path: Path
    pdf_path: Path | None
    log: str
    gaps: list[str] = field(default_factory=list)


def load_content(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "cv_content.yaml"
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing")
    return load_yaml(path)


def _score(text: str, tags: list[str], keys: set[str]) -> int:
    words = _tokens(text) | {t.lower() for t in tags}
    return len(words & keys)


# Requirements an offer may ask for that the CV can only show if the candidate really has them.
# When the offer names one and the tailored CV does not, it is reported as a gap, never added.
GAP_TERMS = {
    "Lean": r"\blean\b", "Kaizen": r"\bkaizen\b", "Six Sigma": r"\bsix sigma\b|\blean six\b",
    "3D printing / additive manufacturing": r"\b3d print|\badditive manufacturing\b",
    "CAD (SolidWorks, CATIA, Creo, NX, AutoCAD)": r"\bcad\b|solidworks|\bcatia\b|\bcreo\b|\bsiemens nx\b|autocad",
    "SAP / ERP": r"\bsap\b|\berp\b", "PLC programming": r"\bplc\b", "FMEA": r"\bfmea\b|\bpfmea\b",
    "Root cause analysis": r"root cause|\brca\b|\b8d\b", "Minitab / SPC": r"minitab|\bspc\b",
    "GD&T": r"gd&t|gd ?& ?t", "SQL": r"\bsql\b", "Power BI": r"power ?bi", "Tableau": r"\btableau\b",
    "MATLAB": r"\bmatlab\b",
}


# Words that show a listing really is about plants, production, maintenance or physical flows.
# A family label alone is not enough: a fintech "operations" role must not get the MRO profile.
_INDUSTRIAL_SIGNAL = re.compile(
    r"manufactur|production|plant\b|factory|shop ?floor|maintenance|\bmro\b|repair|supply chain|logistic|"
    r"warehouse|inventory|lean\b|kaizen|robot|assembly|machin|industrial|cycle time|\boee\b|procurement|"
    r"sourcing|freight|shipment|material")
# Analysis-heavy go-to-market roles (SQL, dashboards, metrics) read better with the product / data profile.
_ANALYTIC_SIGNAL = re.compile(r"\bsql\b|dashboard|analys[ie]s|analytics|metrics|forecast|modeling|excel|tableau")
_SALES_SIGNAL = re.compile(r"prospect|lead generation|pipeline|cold.call|quota|new business|client acquisition|"
                           r"relationship|account management|selling|negotiat")


def industrial_signal(text: str) -> int:
    return len({m.group(0).lower() for m in _INDUSTRIAL_SIGNAL.finditer(text or "")})


def track_for(content: dict[str, Any], job_family: str | None, job_text: str = "") -> str:
    role = role_text(job_text)
    if job_family in set(content.get("industry_families", [])):
        return "industry" if industrial_signal(role) >= 2 else "product"
    if job_family in set(content.get("product_families", [])):
        return "product"
    if job_family == "go_to_market":
        analytic = len(_ANALYTIC_SIGNAL.findall(role.lower()))
        return "product" if analytic > 2 * len(_SALES_SIGNAL.findall(role.lower())) else "business"
    return "business"


def _on_track(item: dict[str, Any], track: str) -> bool:
    return track in item.get("tracks", [track])


# Where a listing states what the job is and asks for, as opposed to company pitch, benefits and legal text.
_ROLE_START = re.compile(r"(?i)\b(job description summary|responsibilit|what you.ll do|your role|role overview|the role|"
                         r"key tasks|job description\b|the impact you will have|"
                         r"your impact|requirements|qualifications|what you.ll need|ideal candidate|you should have|"
                         r"knowledge and skills|skills|your profile|deine aufgaben|about you)")
_ROLE_END = re.compile(r"(?i)\b(what we can offer|what we offer|benefits|equal (employment )?opportunity|"
                       r"recruitment fraud|our commitment to diversity|accommodation|we offer)")


_ROLE_CORE = re.compile(r"(?i)\b(responsibilit|what you.ll do|key tasks|requirements|qualifications|what you.ll need|"
                        r"ideal candidate|you should have|knowledge and skills|your profile|deine aufgaben|role overview|"
                        r"the impact you will have)")


def role_text(job_text: str) -> str:
    """The part of a listing that describes the role and its requirements (whole text when unmarked).

    Benefits and legal text are cut only after the tasks or requirements start, so a company pitch that
    says "what we offer" before the role itself does not swallow the role.
    """
    text = job_text or ""
    start = _ROLE_START.search(text)
    if not start:
        return text
    core = _ROLE_CORE.search(text, start.start())
    end = _ROLE_END.search(text, (core or start).end())
    return text[start.start(): end.start() if end else len(text)]


# Offer themes the candidate can credibly say he wants to contribute to (each backed by the CV).
THEMES = [
    ("continuous improvement", r"continuous improvement|\blean\b|kaizen|process improvement|best practices"),
    ("repair and maintenance processes", r"\brepair|maintenance|\bmro\b|overhaul"),
    ("production quality and productivity", r"\byield\b|production process|production line|manufacturing quality|quality, productivity"),
    ("supply chain and logistics performance", r"supply chain|logistic|inventory|order backlog|demand and supply"),
    ("production planning and digitalization", r"production planning|scheduling|digitali[sz]ation"),
    ("smart factory and automation", r"smart factory|industry 4\.0|robotic|\biot\b"),
    ("process automation", r"automat|\bmacros?\b|\bscripts?\b"),
    ("data analysis and reporting", r"data analys|analytics|dashboard|reporting|insights|data quality"),
    ("AI applications", r"\bai\b|artificial intelligence|machine learning|agentic"),
    ("product discovery and roadmap", r"roadmap|product requirements|customer problem|prototyp|backlog"),
    ("go-to-market and positioning", r"go-to-market|\bgtm\b|positioning|messaging|launch|\bmarketing\b"),
    ("B2B client development", r"lead generation|prospect|pipeline|new business|client acquisition|cold.call"),
    ("solution design for customers", r"solution architect|solution design|presales|pre-sales|sales engineer|tender"),
    ("business requirements and documentation", r"business requirement|functional specification|requirement document"),
    ("cost optimization", r"cost reduction|cost structure|cost optimi"),
]


def target_line(company: str, job_title: str, job_text: str, start: str = "April 2027") -> str:
    """One sentence naming the role and company and the offer's own themes, in the offer's order of emphasis."""
    role = role_text(job_text).lower()
    # Capped counts: a theme the offer repeats in its boilerplate must not outrank its actual tasks.
    # The job title breaks ties: a "Product Marketing" role leads with positioning, not logistics.
    title_l = job_title.lower()
    hits = [(min(len(re.findall(rx, role)), 2) + (1 if re.search(rx, title_l) else 0), i, label)
            for i, (label, rx) in enumerate(THEMES)]
    labels = [label for n, _, label in sorted(hits, key=lambda h: (-h[0], h[1])) if n][:3]
    role = re.sub(r"(?i)\(?\b(intern(ship)?s?|co-op|praktikum|stage|program(me)?)\b\)?|\(\s*\)|[/|]", " ", clean_role(job_title))
    role = re.sub(r"\s+-\s+[A-Z][a-z]+$", "", re.sub(r"\s{2,}", " ", role).strip(" -,:"))   # "... - Amsterdam"
    role = re.sub(r"\s+,", ",", role)
    if not labels or not company.strip() or len(job_title) > 90:
        return ""
    themes = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    what = f"the {role} internship" if len(role) >= 3 else "an internship"
    return f"Seeking {what} at {company.strip()} from {start}, to contribute to {themes}."


def gaps(cv: dict[str, Any], job_title: str, job_text: str) -> list[str]:
    """Offer requirements from GAP_TERMS that the tailored CV does not show."""
    offer = (job_title + " " + role_text(job_text)).lower()
    shown = " ".join([cv.get("summary", "")]
                     + [b["text"] for e in cv.get("experiences", []) for b in e.get("bullets", [])]
                     + [b["text"] for p in cv.get("projects", []) for b in p.get("bullets", [])]
                     + [s for items in (cv.get("skills") or {}).values() for s in items]).lower()
    return [label for label, rx in GAP_TERMS.items() if re.search(rx, offer) and not re.search(rx, shown)]


def _summary(over: dict[str, Any], default: str, job_text: str) -> str:
    text = job_text.lower()
    for variant in over.get("summary_variants", []):
        if len(set(re.findall(variant["when"], text))) >= 2:
            return variant["text"]
    return over.get("summary", default)


def tailor(content: dict[str, Any], job_title: str, job_text: str, job_family: str | None,
           company: str = "") -> dict[str, Any]:
    keys = _tokens(job_title + " " + role_text(job_text)) | set(FAMILY_KEYWORDS.get(job_family or "", "").split())
    track = track_for(content, job_family, job_title + " " + job_text)
    over = (content.get("tracks") or {}).get(track, {})
    headlines = content.get("headlines", {})
    out = dict(content)
    out["track"] = track
    family = job_family or ""
    if track == "product" and family not in set(content.get("product_families", [])):
        family = "data"            # an industry label on a listing that reads like a data / tech role
    out["headline"] = headlines.get(family, headlines.get("default", ""))
    summary = _summary(over, content.get("summary", ""), job_text)
    target = target_line(company, job_title, job_text, content.get("start_label", "April 2027")) if company else ""
    out["summary"] = f"{summary} {target}".strip()
    out["labels"] = dict(content.get("labels", {})) | over.get("labels", {})
    experiences = content.get("experiences", [])
    if over.get("experience_order"):
        by_org = {e.get("org"): e for e in experiences}
        experiences = [by_org[o] for o in over["experience_order"] if o in by_org]
    exps = []
    for exp in experiences:
        pool = [b for b in exp.get("bullets", []) if _on_track(b, track)]
        bullets = sorted(pool, key=lambda b: -_score(b["text"], b.get("tags", []), keys))
        limit = int((over.get("max_bullets") or {}).get(exp.get("org"), exp.get("max_bullets", 3)))
        exps.append(exp | {"bullets": bullets[:limit]})
    out["experiences"] = exps          # chronological order kept, bullets ranked
    ranked = sorted(((_score(" ".join(b["text"] for b in p.get("bullets", [])) + " " + p.get("name", ""), p.get("tags", []), keys), p)
                     for p in content.get("projects", []) if _on_track(p, track)), key=lambda sp: -sp[0])
    limit = int(over.get("max_projects", content.get("max_projects", 3)))
    # A project earns its place by matching the offer; the best one stays so the section is never empty.
    floor = 3 if track == "industry" else 4
    out["projects"] = [p for i, (sc, p) in enumerate(ranked[:limit]) if i == 0 or sc >= floor]
    fixed = set(content.get("fixed_order_skills", []))
    groups = list((over.get("skills") or content.get("skills") or {}).items())
    skills = {group: list(items) if group in fixed else sorted(items, key=lambda s: -len(_tokens(s) & keys))
              for group, items in groups}
    # Most relevant group first; fixed groups (languages) keep their place at the end.
    movable = sorted([g for g, _ in groups if g not in fixed],
                     key=lambda g: -sum(len(_tokens(s) & keys) for s in skills[g]))
    out["skills"] = {g: skills[g] for g in movable + [g for g, _ in groups if g in fixed]}
    out["gaps"] = gaps(out, job_title, job_text)
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
    cv = tailor(content, title, description, job_family, company)
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
    return CVResult(tex_path, pdf_path, log, cv["gaps"])


def gap_note(result: CVResult) -> str:
    """Telegram note listing what the offer asks for and the CV cannot show (empty when nothing)."""
    if not result.gaps:
        return ""
    return ("⚠️ L'offre demande, et ton CV ne montre pas : " + ", ".join(result.gaps)
            + ". Je ne l'ajoute pas sans preuve : si tu l'as vraiment pratiqué, dis-le moi et je l'intègre.")


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
