"""Optional rewrite of a validated draft by the local model (Ollama). The rewrite must pass the same
checks and may not add facts; otherwise it is discarded and the validated draft stays."""

from __future__ import annotations

import re
from typing import Any

from summer_hunter.database.models import OutreachMessage
from summer_hunter.os import messages as msg

PROMPT = """Rewrite this LinkedIn message so it reads naturally, like a confident engineering student wrote it.
Rules: keep every fact exactly as given and add no new fact, name, number, company, skill or claim; keep the greeting,
the request and the signature; no flattery, no "I hope you're doing well", no "passionate", no em dash; same language;
{limit}. Return JSON: {{"text": "..."}}.

MESSAGE:
{text}"""


def rewrite(settings, base: OutreachMessage, contact, opp, app, evidence, interactions, cfg: dict[str, Any]) -> OutreachMessage | None:  # noqa: ANN001
    from summer_hunter.web.letter import build_provider

    limit = ("at most 250 characters" if base.kind == "connection_request"
             else "30 to 70 words" if base.kind in msg.FOLLOW_KINDS else "60 to 110 words")
    provider = build_provider(settings.llm)
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    try:
        resp = provider.complete_json("You edit short professional messages without adding facts.",
                                      PROMPT.format(text=base.text, limit=limit), schema, 600)
        text = str((resp.data or {}).get("text", "")).strip()
    except Exception:  # noqa: BLE001 - the local model is optional
        return None
    if not text:
        return None
    original_names = set(re.findall(r"\b[A-Z][a-zA-Z]+\b", base.text))
    added = set(re.findall(r"\b[A-Z][a-zA-Z]+\b", text)) - original_names - {"I", "I'm", "I'll", "Thank", "Thanks", "Could", "Would",
                                                                            "Hi", "Hello", "Dear", "Best", "Je", "Merci", "Bonjour"}
    draft = msg.Draft(text=text, kind=base.kind, variant=base.variant, language="fr" if base.text.startswith("Bonjour") else "en",
                      evidence_refs=base.evidence_refs or [], personalization=base.personalization)
    draft.validation = msg.validate(draft, contact, app, evidence, interactions, cfg)
    if added:
        draft.validation["issues"].append("new proper nouns: " + ", ".join(sorted(added)))
        draft.validation["ok"] = False
    if not draft.validation["ok"]:
        return None
    return OutreachMessage(contact_id=base.contact_id, opportunity_id=base.opportunity_id, kind=base.kind, variant=base.variant,
                           text=text, generator=f"ollama:{getattr(provider, 'model', 'local')}", evidence_refs=base.evidence_refs,
                           validation=draft.validation, personalization=base.personalization)
