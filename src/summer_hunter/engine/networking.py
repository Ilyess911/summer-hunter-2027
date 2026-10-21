"""Recruitment-intent classification of posts and evidence-based contact extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass

from summer_hunter.models.opportunity import ContactCandidate, PostClass, Relationship
from summer_hunter.utils.text import normalize_for_match, snippet

_HIRING = re.compile(
    r"\b(hiring|we'?re hiring|we are hiring|is hiring|are recruiting|recruiting|looking for|seeking|searching for|"
    r"open (role|position|positions|roles)|now open|applications? (are |is )?(now )?open|apply (now|here|via|through|below)|"
    r"join (my|our) team|nous recrutons|on recrute|je recrute|recherchons|nous cherchons|a pourvoir|accepting applications)\b"
)
_INTERN = re.compile(r"\b(intern(ship)?s?|stagiaire|stage|summer analyst|co-?op|placement|praktikum)\b")
_MY_TEAM = re.compile(
    r"\b(my team|join my team|i'?m hiring|i am hiring|i'?m looking for|i am looking for|i'?m recruiting|i am recruiting|"
    r"mon equipe|je recrute|je cherche|reporting (directly )?to me|work (directly )?with me|in my team|on my team)\b"
)
_UPCOMING = re.compile(r"\b(will be hiring|opening soon|upcoming|in the coming (weeks|months)|stay tuned|will open|prochainement|bientot|soon)\b")
_CAMPAIGN = re.compile(r"\b(internship program(me)?|intern program(me)?|campus recruit\w*|early careers?|class of 20\d\d|cohort|"
                       r"applications for (our|the) 20\d\d|graduate and internship)\b")
_REPOST = re.compile(r"\b(repost\w*|sharing (this|for)|resharing|passing (this )?along|spread the word|h/t|share with your network)\b")
_ADVICE = re.compile(r"\b(tips?|advice|how (to|i) (land|got|get)|lessons|mistakes|cv tips|resume tips|interview tips|career advice|"
                     r"my journey|i (landed|got) an? internship)\b")
_RECRUITER_HEADLINE = re.compile(r"\b(recruit\w*|talent acquisition|talent partner|sourcer|campus|university relations|early careers|"
                                 r"people partner|ressources humaines|charge(e)? de recrutement|human resources|hr business partner)\b")
_JOB_LINK = re.compile(r"(greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs\.com|/careers?/|/jobs?/|lnkd\.in|linkedin\.com/jobs)", re.I)
_COMPANY_IN_HEADLINE = re.compile(r"(?:\bat\b|@|\bchez\b)\s+([A-Z][\w&.'\-]*(?:\s+[A-Z][\w&.'\-]*){0,3})")
_ROLE_PHRASE = re.compile(r"\b((?:[A-Z][\w/&\-]+\s+){1,5})(Intern(?:ship)?s?|Stagiaire)\b")
_VACANCY_CONTACT = re.compile(r"\b(Recruiter|Hiring Manager|Recruiting Contact|Talent Partner)\s*:\s*([A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){1,2})")


@dataclass
class PostClassification:
    label: PostClass
    confidence: float
    reasons: list[str]
    relationship: Relationship | None
    relationship_confidence: float


def classify_post(text: str, author_headline: str | None = None) -> PostClassification:
    norm = normalize_for_match(text)
    head = normalize_for_match(author_headline or "")
    has = lambda rx: bool(rx.search(norm))  # noqa: E731
    reasons = [name for name, rx in (("hiring", _HIRING), ("internship", _INTERN), ("my-team", _MY_TEAM),
                                     ("upcoming", _UPCOMING), ("campaign", _CAMPAIGN), ("repost", _REPOST),
                                     ("advice", _ADVICE)) if rx.search(norm)]
    recruiter = bool(_RECRUITER_HEADLINE.search(head))
    link = bool(_JOB_LINK.search(text or ""))
    if recruiter:
        reasons.append("recruiter-headline")
    if link:
        reasons.append("job-link")

    if not has(_INTERN):
        label = PostClass.CAREER_ADVICE if has(_ADVICE) else PostClass.IRRELEVANT
        return PostClassification(label, 0.6, reasons, None, 0.0)
    if has(_ADVICE) and not has(_HIRING):
        return PostClassification(PostClass.CAREER_ADVICE, 0.7, reasons, None, 0.0)
    if has(_REPOST) and has(_HIRING) and not has(_MY_TEAM):
        return PostClassification(PostClass.REPOST, 0.6, reasons, Relationship.OTHER_EVIDENCE, 0.5)
    if recruiter and has(_HIRING):
        label = PostClass.RECRUITER_WITH_VACANCY if link else (PostClass.GENERAL_CAMPAIGN if has(_CAMPAIGN) else PostClass.CONFIRMED_INTERNSHIP)
        return PostClassification(label, 0.8 if link else 0.65, reasons, Relationship.RECRUITER_POST, 0.85)
    if has(_MY_TEAM) and has(_UPCOMING) and not has(re.compile(r"applications? (are )?(now )?open|apply (now|here)")):
        return PostClassification(PostClass.HIRING_MANAGER_UPCOMING, 0.7, reasons, Relationship.HIRING_MANAGER_POST, 0.75)
    if has(_MY_TEAM) and (has(_HIRING) or has(_INTERN)):
        return PostClassification(PostClass.CONFIRMED_INTERNSHIP, 0.8, reasons, Relationship.HIRING_MANAGER_POST, 0.85)
    if has(_CAMPAIGN) and has(_HIRING):
        return PostClassification(PostClass.GENERAL_CAMPAIGN, 0.7, reasons, Relationship.TEAM_MEMBER_POST, 0.6)
    if has(_HIRING):
        return PostClassification(PostClass.CONFIRMED_INTERNSHIP, 0.6, reasons, Relationship.TEAM_MEMBER_POST, 0.7)
    return PostClassification(PostClass.IRRELEVANT, 0.5, reasons, None, 0.0)


def company_from_headline(headline: str | None) -> str | None:
    if not headline:
        return None
    m = _COMPANY_IN_HEADLINE.search(headline)
    return m.group(1).strip(" .,|") if m else None


def role_from_post(text: str) -> str | None:
    """Exact role phrase as written in the post (e.g. 'Product Marketing Intern'), never synthesized."""
    m = _ROLE_PHRASE.search(text or "")
    if not m:
        return None
    phrase = (m.group(1) + m.group(2)).strip()
    words = phrase.split()
    # Drop leading sentence words such as "Hiring", "Our", "A".
    while words and words[0].lower() in {"hiring", "our", "a", "an", "the", "new", "we", "my", "for", "join", "seeking", "looking"}:
        words = words[1:]
    return " ".join(words) if len(words) >= 2 else None


def contact_from_post(text: str, post_url: str, author_name: str | None, author_profile_url: str | None,
                      author_headline: str | None, company: str | None, cls: PostClassification) -> ContactCandidate | None:
    if not author_name or cls.relationship is None:
        return None
    norm = normalize_for_match(text)
    cue = _MY_TEAM.search(norm) or _HIRING.search(norm) or _INTERN.search(norm)
    evidence = snippet(norm, cue.start(), cue.end(), 120) if cue else (text or "")[:240]
    return ContactCandidate(
        name=author_name.strip()[:120],
        role=(author_headline or None) and author_headline[:160],
        company=company,
        profile_url=author_profile_url,
        source_post_url=post_url,
        relationship=cls.relationship,
        confidence=cls.relationship_confidence,
        evidence=f"Post author. {evidence}",
    )


def contacts_from_vacancy(description: str, company: str, url: str) -> list[ContactCandidate]:
    out = []
    for m in _VACANCY_CONTACT.finditer(description or ""):
        label, name = m.group(1), m.group(2)
        out.append(ContactCandidate(name=name, role=f"{label} (named on the vacancy)", company=company, profile_url=None,
                                    source_post_url=url, relationship=Relationship.NAMED_RECRUITER_ON_VACANCY,
                                    confidence=0.75, evidence=snippet(description, m.start(), m.end(), 80)))
    return out
