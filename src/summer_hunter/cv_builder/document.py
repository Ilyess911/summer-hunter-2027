"""AI CV Builder lot 3: one structured CV, rendered to PDF (LaTeX) and DOCX with identical content.

Pipeline: verified content (cv_content.yaml) + confirmed profile facts
  -> request analysis (intent.py) -> selection and ordering (web.cv.tailor, the hunter's own tailoring)
  -> fact validation -> layout -> PDF + DOCX -> quality checks (pages, dates, sections, PDF/DOCX parity).

No LLM writes CV text here: every line comes from the approved CV or from a fact the candidate
confirmed. The PDF keeps the candidate's exact LaTeX layout (style "ATS Classic"); the DOCX mirrors it
in a single column (no tables, no text boxes) so ATS parsers read both the same way.
"""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from summer_hunter.cv_builder.intent import CVRequest
from summer_hunter.web import cv as cvmod

STYLES = {"ats_classic": "ATS Classic"}          # Modern Professional and Consulting / Business: later
DEFAULT_STYLE = "ats_classic"
MAX_FIT_STEPS = 4

LABELS_FR = {"PROFILE": "PROFIL", "PROFESSIONAL EXPERIENCE": "EXPÉRIENCE PROFESSIONNELLE", "PROJECTS": "PROJETS",
             "TECHNICAL PROJECTS": "PROJETS TECHNIQUES", "LEADERSHIP & ENTREPRENEURSHIP": "LEADERSHIP & ENTREPRENEURIAT",
             "EDUCATION": "FORMATION", "SKILLS & LANGUAGES": "COMPÉTENCES & LANGUES"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Rendered:
    cv: dict[str, Any]
    stem: str
    pdf_path: Path | None
    docx_path: Path | None
    tex_path: Path | None
    pages: int | None
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    style: str = DEFAULT_STYLE

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


# Composition ----------------------------------------------------------------------------------------

def _norm(text: str) -> str:
    flat = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", flat)


def _tokens(text: str) -> set[str]:
    return cvmod._tokens(text)          # same tokenizer as the tailoring


def load_content_for(config_dir: Path, language: str) -> tuple[dict[str, Any], list[str]]:
    """Verified content in the requested language. A French CV needs a validated cv_content_fr.yaml:
    without it the text stays in English (never machine-translated) and only the headings are French."""
    notes: list[str] = []
    content = cvmod.load_content(config_dir)
    if language == "fr":
        fr = config_dir / "cv_content_fr.yaml"
        if fr.exists():
            content = _load_yaml(fr)
        else:
            notes.append("Version française du contenu absente (config/cv_content_fr.yaml) : titres en français, "
                         "texte resté en anglais, jamais traduit automatiquement.")
    return content, notes


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _job_text(req: CVRequest) -> str:
    if req.mode == "offer":
        return req.source_text
    return " ".join([req.source_text, *req.emphasis, *req.expected_skills, *req.sector])


_SYNONYMS = {"cobot": "robot robotics automation", "plm": "product lifecycle digital", "ai": "artificial intelligence",
             "agent": "ai automation", "scan": "imaging", "dental": "healthcare medical"}


def _expand(text: str) -> set[str]:
    toks = _tokens(text)
    for t in list(toks):
        for root, extra in _SYNONYMS.items():
            if t.startswith(root):
                toks |= set(extra.split())
    return toks


def _sentence(text: str) -> str:
    """The candidate's confirmed wording as a CV line: capital first letter, final full stop. Nothing else changes."""
    t = text.strip()
    return (t[:1].upper() + t[1:]).rstrip(".") + "." if t else t


def ranked_extras(content: dict[str, Any], cv: dict[str, Any], req: CVRequest) -> list[dict[str, Any]]:
    """Projects on the CV's track that the tailoring left out, most relevant first (score >= 1)."""
    keys = cvmod._tokens(f"{req.target_role or ''} {cvmod.role_text(_job_text(req))}") | \
        set(cvmod.FAMILY_KEYWORDS.get(req.family, "").split())
    shown = {p.get("name") for p in cv.get("projects", [])}
    excluded = [(_tokens(w) - _GENERIC, _norm(w)) for w in req.exclude]
    pool = [p for p in content.get("projects", []) if p.get("name") not in shown and cvmod._on_track(p, cv.get("track", ""))
            and not any((want and want <= _tokens(p.get("name", ""))) or n in _norm(p.get("name", "")) for want, n in excluded)]
    scored = [(cvmod._score(" ".join(b["text"] for b in p.get("bullets", [])) + " " + p.get("name", ""),
                            p.get("tags", []), keys), p) for p in pool]
    return [p for sc, p in sorted(scored, key=lambda sp: -sp[0]) if sc >= 1]


def compose(content: dict[str, Any], req: CVRequest, confirmed: list[Any]) -> tuple[dict[str, Any], list[str]]:
    """Tailored CV dict for the request, with the candidate-confirmed facts placed where they belong."""
    notes: list[str] = []
    content = copy.deepcopy(content)
    if req.pages >= 2:
        content["max_projects"] = max(int(content.get("max_projects", 2)), 4)
        for over in (content.get("tracks") or {}).values():
            over.pop("max_projects", None)
    for f in confirmed:     # confirmed projects compete with the approved ones in the same ranking
        if f.kind == "project":
            name, _, detail = f.text.partition(":")
            content.setdefault("projects", []).append({"name": name.strip(), "tags": sorted(_expand(f.text)),
                                                       "bullets": [{"text": _sentence(detail or name)}], "confirmed_fact": True})
    company = "" if req.personalization == "light" else (req.company or "")     # light: no targeted sentence
    cv = cvmod.tailor(content, req.target_role or "", _job_text(req), req.family, company, track=req.track)
    keys = _tokens(f"{req.target_role or ''} {_job_text(req)}")
    _place_confirmed(cv, confirmed, keys, notes)
    _apply_emphasis(cv, content, req, notes)
    _apply_exclude(cv, req, notes)
    if req.language == "fr":
        cv["labels"] = {k: LABELS_FR.get(v, v) for k, v in (cv.get("labels") or {}).items()}
        cv["labels"] = {"summary": "PROFIL", "experience": "EXPÉRIENCE PROFESSIONNELLE", "education": "FORMATION",
                        "skills": "COMPÉTENCES & LANGUES"} | cv["labels"]
    if req.style and req.style not in STYLES:
        notes.append(f"Style « {req.style} » pas encore disponible : ATS Classic utilisé.")
    cv["_content"] = content            # with confirmed projects, for the page-filling step
    return cv, notes


def _place_confirmed(cv: dict[str, Any], confirmed: list[Any], keys: set[str], notes: list[str]) -> None:
    skills: dict[str, list[str]] = cv.get("skills") or {}
    fixed = {"Languages"}
    movable = [g for g in skills if g not in fixed]
    tech = next((g for g in movable if re.search(r"tech|data|analytics|ai", g, re.I)), movable[0] if movable else None)
    unplaced = []
    for f in confirmed:
        if f.kind == "skill" and tech:
            if _norm(f.text) not in {_norm(s) for items in skills.values() for s in items}:
                skills[tech].append(f.text)
        elif f.kind == "certification":
            skills.setdefault("Certifications", [])
            if f.text not in skills["Certifications"]:
                skills["Certifications"].append(f.text)
        elif f.kind == "project":
            pass                                # ranked by tailor with the approved projects
        else:
            unplaced.append(f.text)
    if "Certifications" in skills:          # languages stay last
        langs = skills.pop("Languages", None)
        if langs is not None:
            skills["Languages"] = langs
    cv["skills"] = skills
    if unplaced:
        notes.append("Faits confirmés non placés automatiquement (expérience, distinction…) : " + "; ".join(unplaced[:3]))


def _apply_emphasis(cv: dict[str, Any], content: dict[str, Any], req: CVRequest, notes: list[str]) -> None:
    """'Mets en avant X': bring the matching verified project or experience first; never write new text."""
    for what in req.emphasis:
        want = _tokens(what) - _GENERIC
        if not want:
            continue
        pool = list(content.get("projects", []))
        best = max(pool, key=lambda p: len(want & _tokens(p.get("name", "") + " " + " ".join(b["text"] for b in p.get("bullets", [])))),
                   default=None)
        score = len(want & _tokens(best.get("name", "") + " " + " ".join(b["text"] for b in best.get("bullets", [])))) if best else 0
        if best and score >= 1:
            projects = [p for p in cv.get("projects", []) if p.get("name") != best.get("name")]
            cv["projects"] = [best] + projects
            continue
        exps = cv.get("experiences", [])
        hit = next((e for e in exps if want & _tokens(e.get("org", "") + " " + e.get("role", ""))), None)
        if hit:
            cv["experiences"] = [hit] + [e for e in exps if e is not hit]
        else:
            notes.append(f"Rien de vérifié ne correspond à « {what} » : à ajouter d'abord avec /cv_profile add.")


_GENERIC = {"mon", "ma", "mes", "my", "projet", "project", "projects", "projets", "experience", "experiences", "stage",
            "the", "le", "la", "les", "de", "du", "des"}


def _apply_exclude(cv: dict[str, Any], req: CVRequest, notes: list[str]) -> None:
    """'Retire X' for this CV only: drop matching projects, experiences (one always stays) and skills."""
    for what in req.exclude:
        want = _tokens(what) - _GENERIC
        if not want:
            continue

        def hit(text: str) -> bool:
            return want <= _tokens(text) or _norm(what) in _norm(text)
        before = (len(cv.get("projects", [])), len(cv.get("experiences", [])),
                  sum(len(v) for v in (cv.get("skills") or {}).values()))
        cv["projects"] = [p for p in cv.get("projects", []) if not hit(p.get("name", ""))]
        kept = [e for e in cv.get("experiences", []) if not hit(f"{e.get('org', '')} {e.get('role', '')}")]
        cv["experiences"] = kept or cv.get("experiences", [])[:1]
        cv["skills"] = {g: [s for s in items if not hit(s)] for g, items in (cv.get("skills") or {}).items()}
        cv["skills"] = {g: items for g, items in cv["skills"].items() if items}
        after = (len(cv["projects"]), len(cv["experiences"]), sum(len(v) for v in cv["skills"].values()))
        if after == before:
            notes.append(f"« {what} » n'apparaît pas sur ce CV : rien retiré.")


def allowed_texts(content: dict[str, Any], confirmed: list[Any]) -> set[str]:
    """Every sentence the CV may contain, normalized: approved CV text and confirmed facts."""
    out: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            out.add(_norm(node))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(content)
    for f in confirmed:
        out.add(_norm(f.text))
        name, _, detail = f.text.partition(":")
        out |= {_norm(name), _norm(detail)}
    return out


# Plain blocks (shared by DOCX and the checks) -------------------------------------------------------------

def display(text: Any) -> str:
    """The characters LaTeX prints for this source text (-- and --- become dashes)."""
    return str(text or "").replace("---", "—").replace("--", "–")


def blocks(cv: dict[str, Any]) -> list[tuple[str, str]]:
    """(kind, text) in reading order, exactly what both documents print."""
    h, labels = cv.get("header", {}), cv.get("labels", {})
    out: list[tuple[str, str]] = [("name", h.get("name", ""))]
    if cv.get("headline"):
        out.append(("headline", cv["headline"]))
    contact = [c for c in (h.get("email"), h.get("phone")) if c]
    if contact:
        out.append(("contact", "  |  ".join(contact)))
    links = [l["label"] for l in h.get("links", []) if l.get("url")]
    if links:
        out.append(("links", "  |  ".join(links)))
    if h.get("availability"):
        out.append(("availability", "  |  ".join(p.strip() for p in h["availability"].split("|"))))
    if cv.get("summary"):
        out += [("section", labels.get("summary", "PROFILE")), ("text", cv["summary"])]
    out.append(("section", labels.get("experience", "PROFESSIONAL EXPERIENCE")))
    for e in cv.get("experiences", []):
        out.append(("role", f"{e.get('org')}\t{cvmod.ats_dates(e.get('dates'))}\n{e.get('role')}\t{e.get('location')}"))
        out += [("bullet", b["text"]) for b in e.get("bullets", [])]
    if cv.get("projects"):
        out.append(("section", labels.get("projects", "LEADERSHIP & ENTREPRENEURSHIP")))
        for p in cv["projects"]:
            out.append(("project", p.get("name", "")))
            out += [("bullet", b["text"]) for b in p.get("bullets", [])]
    if cv.get("education"):
        out.append(("section", labels.get("education", "EDUCATION")))
        for ed in cv["education"]:
            out.append(("role", f"{ed.get('school')}\t{cvmod.ats_dates(ed.get('dates'))}\n{ed.get('degree')}"))
    if cv.get("skills"):
        out.append(("section", labels.get("skills", "SKILLS & LANGUAGES")))
        out += [("skills", f"{g}: {', '.join(items)}") for g, items in cv["skills"].items()]
    return [(k, display(t)) for k, t in out]


# DOCX -------------------------------------------------------------------------------------------------

def render_docx(cv: dict[str, Any], path: Path) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    navy, gray = RGBColor(0x17, 0x32, 0x4D), RGBColor(0x66, 0x66, 0x66)
    doc = Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    sec.top_margin = sec.bottom_margin = Cm(1.25)
    sec.left_margin = sec.right_margin = Cm(1.58)
    width = sec.page_width - sec.left_margin - sec.right_margin
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.space_before = Pt(0)
    h = cv.get("header", {})

    def para(text: str = "", bold=False, italic=False, size=None, color=None, center=False, after=0, style=None):  # noqa: ANN001, ANN202
        p = doc.add_paragraph(style=style)
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(after)
        if text:
            r = p.add_run(text)
            r.bold, r.italic = bold, italic
            if size:
                r.font.size = Pt(size)
            if color is not None:
                r.font.color.rgb = color
        return p

    def hyperlink(p, url: str, label: str) -> None:  # noqa: ANN001
        rid = p.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                               is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), rid)
        run = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        col = OxmlElement("w:color")
        col.set(qn("w:val"), "17324D")
        rpr.append(col)
        run.append(rpr)
        t = OxmlElement("w:t")
        t.text = label
        run.append(t)
        link.append(run)
        p._p.append(link)

    def rule(p) -> None:  # noqa: ANN001 - bottom border under a section heading
        ppr = p._p.get_or_add_pPr()
        bdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "17324D")):
            bottom.set(qn(k), v)
        bdr.append(bottom)
        ppr.append(bdr)

    def two_sided(left: str, right: str, bold=False, italic=False) -> None:  # noqa: ANN001
        p = para()
        p.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
        r = p.add_run(left)
        r.bold, r.italic = bold, italic
        r2 = p.add_run("\t" + right)
        r2.font.size, r2.font.color.rgb = Pt(9), gray

    for kind, text in blocks(cv):
        if kind == "name":
            para(text, bold=True, size=19, color=navy, center=True, after=3)
        elif kind == "headline":
            para(text, bold=True, size=10, center=True, after=4)
        elif kind == "contact":
            para(text, center=True, after=2)
        elif kind == "links":
            p = para(center=True, after=3)
            items = [l for l in h.get("links", []) if l.get("url")]
            for i, l in enumerate(items):
                if i:
                    p.add_run("  |  ")
                hyperlink(p, l["url"], display(l["label"]))
        elif kind == "availability":
            para(text, bold=True, size=9, center=True, after=2)
        elif kind == "section":
            p = para(text, bold=True, size=10.5, color=navy)
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(4)
            rule(p)
        elif kind == "role":
            lines = text.split("\n")
            left, _, right = lines[0].partition("\t")
            two_sided(left, right, bold=True)
            if len(lines) > 1:
                left, _, right = lines[1].partition("\t")
                if right:
                    two_sided(left, right, italic=True)
                else:
                    para(left)
        elif kind == "project":
            p = para(text, bold=True)
            p.paragraph_format.space_before = Pt(2)
        elif kind == "bullet":
            p = para(text, style="List Bullet")
            p.paragraph_format.space_after = Pt(1.5)
        elif kind == "skills":
            group, _, items = text.partition(": ")
            p = para()
            p.add_run(group + ": ").bold = True
            p.add_run(items)
            p.paragraph_format.space_after = Pt(2)
        else:
            para(text, after=0)
    doc.core_properties.author = h.get("name", "")
    doc.core_properties.title = "CV"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


def docx_text(path: Path) -> str:
    from docx import Document

    doc = Document(path)
    parts = []
    for p in doc.paragraphs:
        parts.append(p.text)
        for link in p._p.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}hyperlink"):
            parts.append("".join(t.text or "" for t in link.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")))
    return "\n".join(parts)


# PDF ---------------------------------------------------------------------------------------------------

def pdf_pages(pdf: Path) -> int | None:
    exe = shutil.which("pdfinfo")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, str(pdf)], capture_output=True, text=True, timeout=20).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else None


def pdf_text(pdf: Path) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        return subprocess.run([exe, "-enc", "UTF-8", str(pdf), "-"], capture_output=True, text=True, timeout=30).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None


def _compile(cv: dict[str, Any], work_dir: Path, target: str) -> tuple[Path | None, Path, int | None, str]:
    work = work_dir / "cv_build.tex"
    work.write_text(cvmod.render(cv, target), encoding="utf-8")
    pdf, log = cvmod.compile_pdf(work)
    return pdf, work, pdf_pages(pdf) if pdf else None, log


def _shrink(cv: dict[str, Any], step: int) -> str | None:
    """One step towards the page limit, least valuable content first. Returns what was cut."""
    if step == 0 and len(cv.get("projects", [])) > 1:
        cv["projects"] = cv["projects"][:1]
        return "projets réduits à 1"
    if step <= 1:
        cut = False
        for e in cv.get("experiences", []):
            if len(e.get("bullets", [])) > 2:
                e["bullets"] = e["bullets"][:-1]
                cut = True
        if cut:
            return "une puce retirée aux expériences les plus longues"
    if step <= 2 and len(cv.get("experiences", [])) > 3:
        dropped = cv["experiences"].pop()
        return f"expérience la moins pertinente retirée ({dropped.get('org')})"
    if step <= 3:
        for e in cv.get("experiences", []):
            if len(e.get("bullets", [])) > 1:
                e["bullets"] = e["bullets"][:-1]
        return "expériences réduites à leurs puces principales"
    return None


# Quality checks ----------------------------------------------------------------------------------------

_DATE = re.compile(r"(\d{2})/(\d{4})\s*-\s*(?:(\d{2})/(\d{4})|(Present|Aujourd'hui))")


def check_dates(cv: dict[str, Any]) -> Check:
    bad = []
    for item in cv.get("experiences", []) + cv.get("education", []):
        raw = cvmod.ats_dates(item.get("dates"))
        m = _DATE.search(raw)
        if not m:
            bad.append(f"{item.get('org') or item.get('school')}: « {raw} »")
            continue
        start = (int(m.group(2)), int(m.group(1)))
        end = (int(m.group(4)), int(m.group(3))) if m.group(3) else (date.today().year, date.today().month)
        if start > end and not m.group(5):
            bad.append(f"{item.get('org') or item.get('school')}: début après la fin")
    return Check("dates", not bad, "; ".join(bad) or "cohérentes")


def check_sections(cv: dict[str, Any]) -> Check:
    missing = [s for s in ("summary", "experiences", "education", "skills") if not cv.get(s)]
    return Check("sections", not missing, ", ".join(missing) or "toutes présentes")


def check_facts(cv: dict[str, Any], allowed: set[str]) -> Check:
    """Every bullet and project line must be approved CV text or a confirmed fact."""
    unknown = [b["text"][:60] for e in cv.get("experiences", []) + cv.get("projects", []) for b in e.get("bullets", [])
               if _norm(b["text"]) not in allowed]
    unknown += [s for items in (cv.get("skills") or {}).values() for s in items if _norm(s) not in allowed]
    return Check("facts", not unknown, "; ".join(unknown[:3]) or "tout provient du profil vérifié")


def check_parity(cv: dict[str, Any], pdf: Path | None, docx: Path | None) -> Check:
    if not pdf or not docx:
        return Check("parity", False, "PDF ou DOCX manquant")
    ptxt, dtxt = pdf_text(pdf), docx_text(docx)
    if ptxt is None:
        return Check("parity", True, "pdftotext absent : comparaison PDF non faite")
    pn, dn = _norm(ptxt), _norm(dtxt)
    missing_pdf, missing_docx = [], []
    for kind, text in blocks(cv):
        for piece in re.split(r"[\t\n]|  \|  ", text):
            n = _norm(piece)
            if len(n) < 3:
                continue
            if n not in pn:
                missing_pdf.append(piece[:50])
            if n not in dn:
                missing_docx.append(piece[:50])
    ok = not missing_pdf and not missing_docx
    detail = "contenu identique" if ok else (f"absent du PDF : {missing_pdf[:2]} ; absent du DOCX : {missing_docx[:2]}")
    return Check("parity", ok, detail)


# Entry point --------------------------------------------------------------------------------------------

def file_stem(req: CVRequest, version: int = 1, year: int = 2027) -> str:
    """CV_Robotics_Engineer_Singapore_2027_v1 (ASCII, Telegram- and filesystem-safe)."""
    role = re.sub(r"\s+[-–|]\s+[^-–|]+$", "", cvmod.clean_role(req.target_role or "CV"))
    role = re.sub(r"(?i)\b(intern(ship)?|stage|stagiaire)\b", "", role)
    parts = [role, req.company or "", req.location if req.country_code else ""]
    words = []
    for p in parts:
        flat = unicodedata.normalize("NFKD", p).encode("ascii", "ignore").decode()
        words += [w for w in re.split(r"[^A-Za-z0-9+]+", flat) if w]
    seen, uniq = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            uniq.append(w[:1].upper() + w[1:])
    return "_".join(["CV", *uniq[:7], str(year), f"v{version}"])


def build(config_dir: Path, out_dir: Path, req: CVRequest, confirmed: list[Any], version: int = 1) -> Rendered:
    content, notes = load_content_for(config_dir, req.language)
    cv, more = compose(content, req, confirmed)
    content = cv.pop("_content", content)
    notes += more
    allowed = allowed_texts(content, confirmed)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = file_stem(req, version)
    target = f"{req.target_role or ''} @ {req.company or req.location}"
    pdf, work, pages, log = _compile(cv, out_dir, target)
    step = 0
    while pdf and pages and pages > req.pages and step < MAX_FIT_STEPS:
        cut = _shrink(cv, step)
        step += 1
        if cut is None:
            break
        notes.append(f"Mise en page : {cut} pour tenir sur {req.pages} page{'s' if req.pages > 1 else ''}.")
        pdf, work, pages, log = _compile(cv, out_dir, target)
    # Room left on the page: add the next relevant projects while the CV still fits.
    limit = 4 if req.pages >= 2 else 2
    if pdf and pages and pages <= req.pages and step == 0:
        for extra in ranked_extras(content, cv, req):
            if len(cv.get("projects", [])) >= limit:
                break
            cv["projects"] = cv.get("projects", []) + [extra]
            pdf2, work2, pages2, log2 = _compile(cv, out_dir, target)
            if pdf2 and pages2 and pages2 <= req.pages:
                pdf, work, pages, log = pdf2, work2, pages2, log2
            else:
                cv["projects"] = cv["projects"][:-1]
                pdf, work, pages, log = _compile(cv, out_dir, target)
                break
    final_pdf = pdf.rename(out_dir / f"{stem}.pdf") if pdf else None
    tex = work.rename(out_dir / f"{stem}.tex")
    docx = render_docx(cv, out_dir / f"{stem}.docx")
    checks = [Check("pdf", final_pdf is not None, "compilé" if final_pdf else log[:200]),
              Check("pages", pages is not None and pages <= req.pages,
                    f"{pages} page(s) pour {req.pages} demandée(s)" if pages else "nombre de pages inconnu"),
              check_dates(cv), check_sections(cv), check_facts(cv, allowed), check_parity(cv, final_pdf, docx)]
    return Rendered(cv, stem, final_pdf, docx, tex, pages, checks, notes)
