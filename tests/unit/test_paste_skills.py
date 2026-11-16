"""Skills to paste into an application form: held skills only, offer matches first, within Telegram's copy limit."""

from pathlib import Path

from summer_hunter.cv_builder.paste_skills import MAX_CHARS, MAX_SKILLS, select
from summer_hunter.utils.settings import load_yaml
from summer_hunter.web.cv import load_content

CONFIG = Path(__file__).resolve().parents[2] / "config"


def content():
    return load_content(CONFIG)


def held(c) -> set[str]:  # noqa: ANN001
    groups = [c.get("skills") or {}, *[(t.get("skills") or {}) for t in (c.get("tracks") or {}).values()]]
    return {s for g in groups for items in g.values() for s in items}


SALES = """Solution Engineer Intern. What you'll do: run customer discovery and demos, build relationships with
stakeholders, use Salesforce CRM daily, support lead generation. Requirements: Python is a plus, strong presentation skills."""
INDUSTRY = """Maintenance Reliability Intern. Missions: analyse maintenance data and KPIs, lead continuous improvement and lean
5S workshops, inventory management of spare parts, Excel and Power BI reporting. Experience with MATLAB appreciated."""


def test_only_held_skills_and_no_limited_or_languages():
    c = content()
    out = select(c, [], "Solution Engineer Intern", SALES, "technical_sales")
    assert out and set(out) <= held(c)
    assert not any("Academic" in s or "Beginner" in s or "Native" in s or "TOEIC" in s for s in out)


def test_offer_matches_come_first():
    out = select(content(), [], "Solution Engineer Intern", SALES, "technical_sales")
    assert "CRM" in out[:4] and "Lead Generation" in out[:6]


def test_industry_offer_gets_industry_skills_and_confirmed_facts_only_when_asked():
    out = select(content(), ["MATLAB/Simulink", "C++"], "Maintenance Reliability Intern", INDUSTRY, "maintenance_reliability")
    for s in ("Continuous Improvement", "5S", "Inventory Management", "MATLAB/Simulink"):
        assert s in out
    assert "C++" not in out                     # confirmed, but the offer does not ask for it
    assert "Consultative Selling" not in out     # sales filler never pads an industrial offer


def test_limits():
    out = select(content(), [], "Solution Engineer Intern", SALES * 5, "technical_sales")
    assert len(out) <= MAX_SKILLS and len(", ".join(out)) <= MAX_CHARS


def test_card_shows_and_copies_the_same_list():
    from summer_hunter.database.models import Opportunity
    from summer_hunter.notifications.formatter import buttons

    opp = Opportunity(id=7, title="x", company_name="y", apply_url="https://a.example/x")
    rows = buttons(opp, None, None, ["CRM", "Python"])
    copy = [b for r in rows for b in r if "copy_text" in b]
    assert copy and copy[0]["copy_text"]["text"] == "CRM, Python"
    assert not [b for r in buttons(opp, None) for b in r if "copy_text" in b]
    assert load_yaml(CONFIG / "cv_content.yaml")      # config readable from tests
