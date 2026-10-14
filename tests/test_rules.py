from datetime import date

import pytest
from pydantic import ValidationError

from app.rules import REGISTRY, get_rule


def run(name, **kw):
    return get_rule(name).evaluate(kw)


class TestAnnualLeave:
    def test_seniority_bonus_starts_month_after_anniversary(self):
        # 6th anniversary on 2025-03-15 -> bonus goes from 1 to 2 days from April.
        r = run("annual_leave_balance", start_date="2019-03-15", as_of="2025-06-30", days_taken=8)
        assert r.result["accrued_days"] == round(3 * 26 / 12 + 3 * 27 / 12, 2) == 13.25
        assert r.result["balance_days"] == 5.25

    def test_mid_month_joiner_accrues_from_next_month(self):
        r = run("annual_leave_balance", start_date="2025-01-10", as_of="2025-03-31")
        assert r.result["months_accrued"] == 2  # Feb + Mar

    def test_incomplete_month_not_credited(self):
        r = run("annual_leave_balance", start_date="2020-01-01", as_of="2025-01-30")
        assert r.result["months_accrued"] == 0

    def test_fte_proration(self):
        r = run("annual_leave_balance", start_date="2024-07-10", as_of="2025-02-28", days_taken=2, fte=0.6)
        assert r.result["balance_days"] == 0.5

    def test_carryover_capped_then_expires(self):
        before = run("annual_leave_balance", start_date="2015-01-01", as_of="2025-03-31", carryover_days=7, days_taken=3)
        after = run("annual_leave_balance", start_date="2015-01-01", as_of="2025-04-30", carryover_days=7, days_taken=3)
        assert before.result["carryover_days"] == 5 and before.result["balance_days"] == 9.0
        assert after.result["carryover_days"] == 0
        assert any("expired" in w for w in after.warnings)

    def test_seniority_bonus_capped_at_three(self):
        r = run("annual_leave_balance", start_date="1990-01-01", as_of="2025-12-31")
        assert r.result["accrued_days"] == 28.0

    def test_as_of_before_start_rejected(self):
        with pytest.raises(ValidationError):
            run("annual_leave_balance", start_date="2025-05-01", as_of="2025-01-01")


class TestNotice:
    @pytest.mark.parametrize(
        "level,start,as_of,months",
        [
            ("individual_contributor", "2024-01-01", "2025-06-01", 1),
            ("individual_contributor", "2022-01-01", "2025-06-01", 2),
            ("individual_contributor", "2015-01-01", "2025-06-01", 3),
            ("manager", "2024-01-01", "2025-06-01", 2),
            ("executive", "2018-06-01", "2025-06-02", 6),
        ],
    )
    def test_table(self, level, start, as_of, months):
        assert run("notice_period", level=level, start_date=start, as_of=as_of).result["notice_months"] == months

    def test_exactly_five_years_is_middle_bucket(self):
        r = run("notice_period", level="individual_contributor", start_date="2020-06-01", as_of="2025-06-01")
        assert r.result["notice_months"] == 2

    def test_probation_depends_on_level(self):
        ic = run("notice_period", level="individual_contributor", start_date="2025-01-06", as_of="2025-05-01")
        mgr = run("notice_period", level="manager", start_date="2025-01-06", as_of="2025-05-01")
        assert ic.result["on_probation"] is False
        assert mgr.result["on_probation"] is True and mgr.result["notice_weeks"] == 2

    def test_longer_contract_prevails_shorter_does_not(self):
        base = dict(level="individual_contributor", start_date="2024-01-15", as_of="2025-03-01")
        assert run("notice_period", **base, contract_notice_months=3).result["notice_months"] == 3
        assert run("notice_period", **base, contract_notice_months=0.5).result["notice_months"] == 1


class TestOvertime:
    def test_all_bands(self):
        r = run("overtime_pay", hourly_rate=30, hours_worked=52, public_holiday_hours=8)
        assert r.result["total_pay"] == 1200 + 300 + 180 + 480
        assert r.result["overtime_pay"] == 960

    def test_no_overtime_under_40(self):
        assert run("overtime_pay", hourly_rate=20, hours_worked=38).result["overtime_pay"] == 0

    def test_exempt(self):
        r = run("overtime_pay", hourly_rate=40, hours_worked=50, exempt=True)
        assert r.result == {"overtime_pay": 0.0, "exempt": True}

    def test_flags_60_hour_breach(self):
        r = run("overtime_pay", hourly_rate=20, hours_worked=58, public_holiday_hours=4)
        assert any("compliance" in w for w in r.warnings)


class TestExpenses:
    def test_tier1_meal_capped(self):
        r = run("expense_check", category="meals", amount=95, city="Paris")
        assert r.result == {"status": "partially_approved", "reimbursable_amount": 75.0}

    def test_receipt_threshold_is_inclusive(self):
        assert run("expense_check", category="other", amount=25, has_receipt=False).result["status"] == "rejected"
        assert run("expense_check", category="other", amount=24.99, has_receipt=False).result["status"] == "approved"

    def test_pre_approval_above_1000(self):
        assert run("expense_check", category="hotel", amount=1500, city="Tokyo", units=3).result["status"] == "needs_pre_approval"
        assert run("expense_check", category="hotel", amount=1500, city="Tokyo", units=3, pre_approved=True).result["status"] == "approved"

    def test_late_submission(self):
        assert run("expense_check", category="meals", amount=30, days_since_expense=91).result["status"] == "rejected"
        assert run("expense_check", category="meals", amount=30, days_since_expense=45).warnings

    def test_client_entertainment_per_attendee(self):
        assert run("expense_check", category="client_entertainment", amount=500, attendees=4).result["reimbursable_amount"] == 480


class TestRemote:
    def test_remote_first(self):
        r = run("remote_work_eligibility", role="Senior Backend Developer", probation_completed=True)
        assert r.result["max_remote_days_per_week"] == 5

    def test_standard_role(self):
        r = run("remote_work_eligibility", role="accountant", probation_completed=True)
        assert r.result["max_remote_days_per_week"] == 3

    @pytest.mark.parametrize(
        "kw",
        [
            dict(role="lab technician", probation_completed=True),
            dict(role="data analyst", probation_completed=False),
            dict(role="designer", probation_completed=True, on_pip=True),
            dict(role="designer", probation_completed=True, performance_rating="below"),
        ],
    )
    def test_blockers(self, kw):
        assert run("remote_work_eligibility", **kw).result["eligible"] is False


def test_every_rule_cites_a_policy_section():
    for r in REGISTRY.values():
        assert r.description, r.name
        assert r.schema()["properties"], r.name


def test_rules_are_deterministic():
    kw = dict(start_date=date(2019, 3, 15), as_of=date(2025, 6, 30), days_taken=8)
    a, b = run("annual_leave_balance", **kw), run("annual_leave_balance", **kw)
    assert a.result == b.result and a.steps == b.steps
