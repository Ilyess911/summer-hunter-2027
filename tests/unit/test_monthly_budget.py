"""Monthly cap on all paid calls: cycle from the 11th, shared by analysis, emails and the off-market scout."""

from __future__ import annotations

from datetime import date

from summer_hunter.database.migrations import init_db, make_engine
from summer_hunter.database.models import LLMUsage
from summer_hunter.utils import budget


def test_cycle_starts_on_the_11th():
    assert budget.cycle_start(date(2026, 11, 27)) == date(2026, 11, 11)
    assert budget.cycle_start(date(2026, 11, 11)) == date(2026, 11, 11)
    assert budget.cycle_start(date(2026, 12, 10)) == date(2026, 11, 11)
    assert budget.cycle_start(date(2027, 1, 5)) == date(2026, 12, 11)


def test_month_left_sums_every_purpose_in_the_cycle(tmp_path):
    S = init_db(make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}"))
    with S() as s:
        for d, cost, purpose in [("2026-11-10", 9.0, "job"), ("2026-11-12", 10.0, "job"),
                                 ("2026-11-20", 8.0, "founder_scout"), ("2026-11-27", 3.5, "application_email")]:
            s.add(LLMUsage(local_date=d, provider="anthropic", model="m", purpose=purpose, cost_usd=cost))
        s.commit()
        cfg = {"monthly_budget_usd": 25, "monthly_warn_usd": 20}
        assert budget.month_spent(s, date(2026, 11, 27)) == 21.5      # the 10 Nov row belongs to the last cycle
        assert budget.month_left(s, date(2026, 11, 27), cfg) == 3.5
        assert "21.50 $ / 25 $" in budget.month_line(s, date(2026, 11, 27), cfg) and "⚠️" in budget.month_line(s, date(2026, 11, 27), cfg)
        assert budget.month_left(s, date(2026, 12, 11), cfg) == 25.0   # new credit, new cycle
