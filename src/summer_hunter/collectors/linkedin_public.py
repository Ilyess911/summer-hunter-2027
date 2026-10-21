"""Public LinkedIn discovery through authorized search APIs, plus manual post/job ingestion.

linkedin.com's robots.txt forbids automated access without permission, so this module never
requests linkedin.com. It only reads what an authorized search API returns (URL, title, snippet)
or what the user pastes in manually. Restricted content (full post text behind login, private
profiles) is never collected, and nothing here claims otherwise.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit, urlunsplit

from summer_hunter.collectors.base import CollectContext, Collector
from summer_hunter.engine.networking import (
    PostClassification,
    classify_post,
    company_from_headline,
    contact_from_post,
    role_from_post,
)
from summer_hunter.integrations.search_providers import SearchProvider
from summer_hunter.models.opportunity import ACTIONABLE_POST_CLASSES, LinkedInPostInfo, PostClass, RawOpportunity, Relationship
from summer_hunter.utils.http import HttpError
from summer_hunter.utils.text import clean_text, sha256

log = logging.getLogger(__name__)

_POST_URL = re.compile(r"^https?://([a-z]{2,3}\.)?(www\.)?linkedin\.com/(posts|feed/update)/", re.I)
_JOB_URL = re.compile(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d{6,})", re.I)
_AUTHOR_TITLE = [re.compile(p) for p in (r"^(.{3,80}?) on LinkedIn\s*[:|]", r"^(.{3,80}?)'s Post\b", r"^(.{3,80}?) posted on LinkedIn")]
_JOB_TITLE = re.compile(r"^(?P<company>.+?) hiring (?P<role>.+?) in (?P<loc>.+?)\s*\|\s*LinkedIn", re.I)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = re.sub(r"^[a-z]{2,3}\.linkedin\.com$", "www.linkedin.com", parts.netloc.lower())
    return urlunsplit(("https", host, parts.path.rstrip("/") + "/", "", ""))


def author_from_result_title(title: str) -> str | None:
    for rx in _AUTHOR_TITLE:
        m = rx.search(title or "")
        if m:
            name = m.group(1).strip(" -|")
            if 2 <= len(name.split()) <= 5 and not re.search(r"\b(hiring|jobs?|intern)\b", name, re.I):
                return name
    return None


def build_post(post_url: str, text: str, author_name: str | None, author_profile_url: str | None,
               author_headline: str | None, discovery_method: str, confidence_scale: float = 1.0,
               analyzer=None) -> LinkedInPostInfo:  # noqa: ANN001
    cls = classify_post(text, author_headline)
    post = LinkedInPostInfo(
        post_url=canonical_url(post_url), author_name=author_name, author_profile_url=author_profile_url,
        author_headline=author_headline, text=clean_text(text)[:2000], classification=cls.label,
        classification_confidence=round(cls.confidence * confidence_scale, 2), classification_reasons=cls.reasons,
        relationship=cls.relationship, relationship_confidence=round(cls.relationship_confidence * confidence_scale, 2),
        discovery_method=discovery_method,
    )
    if analyzer is not None:
        refine_post(post, analyzer)
    return post


_REL_MAP = {"hiring_manager": Relationship.HIRING_MANAGER_POST, "recruiter": Relationship.RECRUITER_POST,
            "team_member": Relationship.TEAM_MEMBER_POST, "reposter": Relationship.OTHER_EVIDENCE}


def refine_post(post: LinkedInPostInfo, analyzer) -> None:  # noqa: ANN001
    """Semantic second opinion for uncertain rule-based labels. A confident LLM verdict replaces the
    label; a relationship or role is only accepted with a verbatim quote from the post."""
    from summer_hunter.engine.ai_analysis import classify_post_llm

    if post.classification_confidence >= 0.8:
        return
    outcome = classify_post_llm(analyzer, post.text, post.author_headline)
    v = outcome.verdict
    if v is None or v.injection_attempt_detected or v.confidence < 70:
        return
    scale = 0.8 if post.discovery_method.startswith("search:") else 1.0
    post.classification = PostClass(v.label)
    post.classification_confidence = round(max(post.classification_confidence, v.confidence / 100 * 0.9 * scale), 2)
    post.classification_reasons.append(f"semantic:{v.label}")
    if v.author_relationship in _REL_MAP and outcome.relationship_verified:
        post.relationship = _REL_MAP[v.author_relationship]
        post.relationship_confidence = round(0.8 * scale, 2)
    if outcome.role_verified:
        post.role_hint = v.role_quote.strip()


def opportunity_from_post(post: LinkedInPostInfo, company: str | None = None, location: str | None = None,
                          title: str | None = None) -> RawOpportunity | None:
    """Create an opportunity record from an actionable post. Requires a known company; the role
    title is the exact phrase from the post, or an explicit 'unspecified role' label."""
    if post.classification not in ACTIONABLE_POST_CLASSES:
        return None
    company = company or company_from_headline(post.author_headline)
    if not company:
        return None
    role = title or post.role_hint or role_from_post(post.text) or "Internship (role not specified in post)"
    cls = PostClassification(post.classification, post.classification_confidence, post.classification_reasons,
                             post.relationship, post.relationship_confidence)
    contact = contact_from_post(post.text, post.post_url, post.author_name, post.author_profile_url,
                                post.author_headline, company, cls)
    return RawOpportunity(
        source="linkedin_post", source_id=sha256(post.post_url)[:24], url=post.post_url, apply_url=None,
        title=role, company=company, location_text=location, description_text=post.text,
        posted_at=post.published_at, contacts=[contact] if contact else [], linkedin_post=post,
        extra={"origin": post.discovery_method},
    )


class LinkedInSearchCollector(Collector):
    name = "linkedin_search"

    def __init__(self, provider: SearchProvider | None, post_queries: list[str], job_queries: list[str],
                 max_queries: int = 15, per_query: int = 10) -> None:
        self.provider = provider
        self.post_queries = post_queries
        self.job_queries = job_queries
        self.max_queries = max_queries
        self.per_query = per_query

    async def collect(self, ctx: CollectContext):
        if self.provider is None:
            return self.result(status="disabled", detail="No search API key (BRAVE_SEARCH_API_KEY or GOOGLE_CSE_API_KEY/CX).")
        posts: dict[str, LinkedInPostInfo] = {}
        records: dict[str, RawOpportunity] = {}
        errors: list[str] = []
        queries = [("post", f"site:linkedin.com/posts {q}") for q in self.post_queries] + \
                  [("job", f"site:linkedin.com/jobs/view {q}") for q in self.job_queries]
        for kind, query in queries[: self.max_queries]:
            try:
                results = await self.provider.search(ctx.http, query, self.per_query)
            except (HttpError, ValueError) as exc:
                errors.append(f"{self.provider.name}: {exc}")
                if getattr(exc, "status", None) in (401, 403, 429):
                    break
                continue
            for r in results:
                if kind == "post" and _POST_URL.search(r.url):
                    post = build_post(r.url, f"{r.title}\n{r.snippet}", author_from_result_title(r.title), None, None,
                                      f"search:{self.provider.name}", confidence_scale=0.8)
                    posts.setdefault(post.post_url, post)
                elif kind == "job":
                    m_id, m_title = _JOB_URL.search(r.url), _JOB_TITLE.search(r.title or "")
                    if not (m_id and m_title):
                        continue
                    role = m_title.group("role").strip()
                    if not ctx.title_rules.looks_like_internship(role):
                        continue
                    job_id = m_id.group(1)
                    records[job_id] = RawOpportunity(
                        source=self.name, source_id=job_id, url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                        apply_url=f"https://www.linkedin.com/jobs/view/{job_id}/", title=role,
                        company=m_title.group("company").strip(), location_text=m_title.group("loc").strip(),
                        description_text=clean_text(r.snippet), extra={"origin": f"search:{self.provider.name}"},
                    )
        status = "failed" if errors and not posts and not records else ("partial" if errors else "ok")
        return self.result(records=list(records.values()), posts=list(posts.values()), errors=errors,
                           scanned=len(queries[: self.max_queries]), status=status)
