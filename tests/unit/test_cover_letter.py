"""Cover letters: plan verification, content rules, swap test, minimalist one-page template."""

from __future__ import annotations

from summer_hunter.integrations.anthropic_client import FakeProvider, LLMResponse
from summer_hunter.web.letter import check_letter, experience_facts, facts_block, generate_letter, load_content

POSTING = ("Acme builds warehouse robotics software for logistics operators. As a Solutions Engineering intern you will "
           "prepare product demonstrations for logistics customers, run discovery calls with operations managers, and "
           "translate fleet telemetry questions into integration proposals with our robotics platform team.")
TITLE = "Solutions Engineering Intern - Summer 2027"

GOOD = {
    "p1": ("I am a final-year engineering student at ESILV, and the Solutions Engineering internship at Acme interests me "
           "because it combines warehouse robotics with direct work alongside logistics operators. Preparing product "
           "demonstrations and turning fleet telemetry questions into integration proposals is the kind of problem I want "
           "to work on, at the point where an engineering answer has to make sense for an operations manager."),
    "p2": ("At Air France Industries, I develop AI-enabled tools and automated workflows that improve operational efficiency, "
           "data reliability and decision-making, which has taught me to start from operational data before proposing a fix. "
           "As co-founder of Nexya Agency, I engaged industrial decision-makers on an AI-powered recruitment solution, "
           "discussing technical integration, business requirements and pricing considerations, which is close to the "
           "discovery work this role describes."),
    "p3": ("On your platform team, I would bring a habit of documenting what a customer actually needs before building the "
           "proposal, and the ability to explain the technical answer in operational terms. The internship fits my aim of "
           "building a career in technical sales for industrial software. I am available between April and August 2027 "
           "and would value a conversation about the demonstrations your team prepares."),
}


def ctx(settings):
    content = load_content(settings.config_dir)
    exp = experience_facts(content, ["python", "power query"])
    return exp, facts_block(content, exp)


def run(settings, paras, chosen=("air_france", "nexya")):
    exp, facts = ctx(settings)
    return check_letter(paras, POSTING, TITLE, "Acme", facts, exp, list(chosen))


def test_good_letter_passes(settings):
    assert run(settings, GOOD) == []


def test_repeated_experience_and_extra_evidence_are_rejected(settings):
    bad = dict(GOOD, p3="At Air France Industries I also learned a lot. " + GOOD["p3"])
    assert any("appears in paragraphs" in i for i in run(settings, bad))
    bad = dict(GOOD, p2=GOOD["p2"] + " At Amundi, I supported the distribution team by maintaining CRM data.")
    assert any("planned experiences only" in i for i in run(settings, bad))


def test_length_filler_inventions_are_rejected(settings):
    long = dict(GOOD, p3=" ".join([GOOD["p3"]] * 3))
    assert any("too long" in i for i in run(settings, long))
    fluffy = dict(GOOD, p1="I am passionate and thrilled about this unique opportunity. " + GOOD["p1"])
    assert any("filler" in i for i in run(settings, fluffy))
    invented = dict(GOOD, p2=GOOD["p2"] + " I increased conversion by 35 using SQL.")
    issues = " ".join(run(settings, invented))
    assert "35" in issues and "sql" in issues


def test_swap_test_rejects_generic_letters(settings):
    generic = dict(GOOD, p1=("I am a final-year engineering student at ESILV, and this internship at Acme interests me because "
                             "it combines technology and business. I want to grow in an international team, learn from "
                             "experienced colleagues and contribute to projects that matter to the company and its clients."))
    assert any("not personalized" in i for i in run(settings, generic))


def test_misattribution_is_rejected(settings):
    wrong = dict(GOOD, p2=GOOD["p2"].replace("As co-founder of Nexya Agency", "At Medical'IA"))
    issues = run(settings, wrong, chosen=("air_france", "medicalia"))
    assert any("belongs to Nexya Agency" in i for i in issues)


class Seq(FakeProvider):
    def __init__(self, payloads):
        super().__init__()
        self.payloads = list(payloads)

    def complete_json(self, system, user, schema, max_tokens):
        self.calls.append((system, user))
        return LLMResponse(data=self.payloads.pop(0), input_tokens=0, output_tokens=0, model="fake")


PLAN = {"requirements": [{"requirement": "demos", "quote": "prepare product demonstrations for logistics customers"},
                         {"requirement": "invented", "quote": "ten years of Kubernetes"}],
        "company_details": [{"detail": "robotics software", "quote": "Acme builds warehouse robotics software"}],
        "experiences": ["air_france", "nexya"], "purposes": {"p1": "a", "p2": "b", "p3": "c"}}


def test_generation_plans_revises_and_renders_minimal_one_page(settings, tmp_path):
    bad = dict(GOOD, p2=GOOD["p2"] + " I raised revenue by 40%.")
    provider = Seq([PLAN, bad, GOOD])
    r = generate_letter(settings.config_dir, tmp_path, provider, settings.llm, 1, "Acmé & Co", TITLE, "Singapore", POSTING,
                        job_family="technical_sales")
    assert r.attempts == 2 and r.warnings == []
    assert len(r.analysis["plan"]["requirements"]) == 1          # unverifiable quote dropped
    assert "FIX THESE PROBLEMS" in provider.calls[2][1]
    assert r.analysis["pages"] == 1 and 180 <= r.analysis["words"] <= 270
    tex = r.tex_path.read_text()
    assert "Acmé \\& Co" in tex and f"Application -- {TITLE}" in tex and "{{" not in tex
    for absent in ("\\href", "\\color", "hrule", "LinkedIn", "GitHub"):
        assert absent not in tex
    assert r.named_pdf.name.startswith("LM I.ASSADI - Acmé & Co - Solutions Engineering Intern")
    for name in ("cover_letter.tex", "cover_letter.pdf", "job_description.txt", "analysis.json"):
        assert (r.folder / name).exists()
