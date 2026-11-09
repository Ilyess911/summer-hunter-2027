"""Statuses read from recruiter mail: classification, matching, conservative updates."""

from __future__ import annotations

from datetime import date, datetime, timezone
from email.message import EmailMessage

import pytest

from summer_hunter.crm import mail_status as ms
from summer_hunter.crm.service import CRM
from tests.integration.test_crm import make_opp


@pytest.fixture
def crm(sessions, settings):
    with sessions() as session:
        yield CRM(session, settings.data_dir, settings.timezone)


def applied(crm, n, company, title, on=date(2026, 10, 20)):  # noqa: ANN001
    opp = make_opp(crm.s, n, company_name=company, normalized_company=company.lower(), title=title)
    return crm.confirm_applied(opp, "cli", applied_on=on)


def mail(subject, text, sender="Talent <no-reply@myworkday.com>", mid="<m1@x>"):  # noqa: ANN001
    return ms.MailItem(mid, sender, subject, text, datetime.now(timezone.utc))


@pytest.mark.parametrize("subject,text,kind", [
    ("Your application", "Unfortunately, we have decided to move forward with other candidates.", ms.REJECTED),
    ("Update", "After careful consideration we will not be moving forward with your application.", ms.REJECTED),
    ("Votre candidature", "Nous avons le regret de vous informer que nous ne pouvons pas donner suite.", ms.REJECTED),
    ("Candidature", "Votre candidature n'a pas été retenue.", ms.REJECTED),
    ("AIRBUS - Le statut de votre candidature a été mis à jour",
     "Merci pour votre candidature au poste de Aircraft Sales Gov./Mil Intern. Après l'avoir examinée avec attention, "
     "nous sommes au regret de vous informer qu'elle ne répond pas à nos exigences pour ce poste.", ms.REJECTED),
    ("Mise à jour", "Nous sommes au regret de vous informer que le poste a été annulé.", ms.CLOSED),
    ("Requisition update", "The position has been cancelled.", ms.CLOSED),
    ("Next steps", "We would like to invite you to an interview with the team.", ms.INTERVIEW),
    ("Entretien", "Nous souhaitons vous proposer un entretien la semaine prochaine.", ms.INTERVIEW),
    ("Assessment", "Please complete the online assessment on HackerRank.", ms.ASSESSMENT),
    ("Thank you for applying", "We received your application. If selected for an interview, we will contact you.", None),
    ("Weekly digest", "Top stories this week.", None),
    ("Application received", "If you have not been selected within 4 weeks, please consider other roles.", None),
    ("Update", "We regret to inform you that the position has been cancelled.", ms.CLOSED),
    ("Re: hello", "Thanks!\n\nOn Mon, 2 Nov 2026 at 10:00, Recruiter <r@x.com> wrote:\n> We will not be moving forward with your application.", None),
    ("Tips", "Practice on HackerRank before your next interview.", None),
    ("Mixed", "We would like to invite you to an interview. Unfortunately we will not be moving forward with your application.", None),
])
def test_classify(subject, text, kind):
    assert ms.classify(subject, text) == kind


def test_single_application_rejection_updates_status(crm):
    app = applied(crm, 1, "Databricks", "Product Management Intern (2027) - Amsterdam")
    r = ms.apply(crm, [mail("Your application to Databricks", "Unfortunately we will not be moving forward.")])
    assert app.status == "REJECTED" and len(r.changed) == 1
    again = ms.apply(crm, [mail("Your application to Databricks", "Unfortunately we will not be moving forward.")])
    assert again.changed == []          # final status: the same mail scanned twice changes nothing


def test_same_company_needs_title_to_pick_one(crm):
    a = applied(crm, 1, "Philips", "Strategic Solution Architect Intern")
    b = applied(crm, 2, "Philips", "Production Engineering Internship")
    r = ms.apply(crm, [mail("Philips - Production Engineering Internship", "We regret to inform you that you were not selected.")])
    assert b.status == "REJECTED" and a.status == "APPLIED" and len(r.changed) == 1
    c = applied(crm, 3, "Philips", "IT AI Solution Analyst Intern")
    r = ms.apply(crm, [mail("Your Philips application", "We regret to inform you...", mid="<m2@x>")])
    assert a.status == "APPLIED" and c.status == "APPLIED" and len(r.unmatched) == 1   # ambiguous: nothing changes


def test_acronym_and_no_downgrade(crm):
    app = applied(crm, 1, "Hewlett Packard Enterprise", "Supply Chain Intern")
    ms.apply(crm, [mail("HPE interview invitation", "We would like to schedule an interview.", mid="<a@x>")])
    assert app.status == "INTERVIEW"
    ms.apply(crm, [mail("HPE assessment", "Please complete the online assessment.", mid="<b@x>")])
    assert app.status == "INTERVIEW"     # never moves backwards


def test_unrelated_company_or_pre_application_is_ignored(crm):
    opp = make_opp(crm.s, 1, company_name="Stripe", normalized_company="stripe")
    crm.track(opp, "cli")              # TO_APPLY, never submitted
    r = ms.apply(crm, [mail("Stripe update", "Unfortunately we decided to move forward with other candidates.")])
    assert r.changed == [] and crm.application_for(opp.id).status == "TO_APPLY"
    r = ms.apply(crm, [mail("Acme update", "Unfortunately we decided to move forward with other candidates.")])
    assert r.changed == [] and r.unmatched == []


def test_mail_older_than_application_is_ignored(crm):
    app = applied(crm, 1, "Maersk", "Lead Logistics – System Intern", on=date(2026, 10, 30))
    old = mail("Maersk", "Unfortunately you were not selected.")
    old.received = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ms.apply(crm, [old])
    assert app.status == "APPLIED"


def test_body_only_company_mention_is_reported_not_applied(crm):
    app = applied(crm, 1, "Wise", "Product Analyst Intern")
    r = ms.apply(crm, [mail("Your application", "Hi, unfortunately we will not be moving forward with your application at Wise.",
                            sender="Careers <no-reply@greenhouse.io>")])
    assert app.status == "APPLIED" and len(r.unmatched) == 1 and r.changed == []


def test_offer_is_never_overwritten_and_manual_correction_sticks(crm):
    app = applied(crm, 1, "Stripe", "Financial Data Analyst Intern")
    crm.set_status(app, "OFFER_RECEIVED", "cli")
    ms.apply(crm, [mail("Stripe", "Unfortunately we will not be moving forward with your application.")])
    assert app.status == "OFFER_RECEIVED"
    other = applied(crm, 2, "NVIDIA", "Applied Research Intern")
    m = mail("NVIDIA interview invitation", "We would like to schedule an interview.", mid="<n@x>")
    ms.apply(crm, [m])
    assert other.status == "INTERVIEW"
    crm.set_status(other, "APPLIED", "cli", force=True)
    ms.apply(crm, [m])
    assert other.status == "APPLIED"


def test_html_only_body_and_report_text(crm):
    msg = EmailMessage()
    msg["From"], msg["Subject"], msg["Message-ID"] = "GE Vernova <careers@gevernova.com>", "Your candidacy", "<h@x>"
    msg.set_content("<p>Unfortunately, we have decided <b>not to move forward</b> with your application.</p>"
                    "<blockquote>We would like to invite you to an interview.</blockquote>", subtype="html")
    item = ms.to_item(msg, datetime.now(timezone.utc))
    assert "not to move forward" in item.text and "invite you" not in item.text
    applied(crm, 1, "GE Vernova", "Manufacturing Internship")
    r = ms.apply(crm, [item])
    text = ms.report_text(r)
    assert text and "Refus" in text and "GE Vernova" in text
    assert ms.report_text(ms.MailReport()) is None


def test_sync_is_disabled_without_imap(settings, sessions):
    settings.imap_host = settings.imap_user = settings.imap_password = None
    assert ms.sync_mailbox(settings, sessions) is None
