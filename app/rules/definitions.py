import calendar
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.rules.engine import RuleOutcome, rule


def _money(x: float) -> str:
    return f"{x:,.2f} USD"


def _full_years(start: date, end: date) -> int:
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(years, 0)


def _month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


# --------------------------------------------------------------------------- annual leave


class LeaveBalanceInput(BaseModel):
    start_date: date = Field(description="Employment start date (YYYY-MM-DD)")
    as_of: date = Field(default_factory=date.today, description="Date of the calculation (YYYY-MM-DD)")
    days_taken: float = Field(0, ge=0, description="Annual leave days already taken this calendar year")
    carryover_days: float = Field(0, ge=0, description="Unused days carried over from last year")
    fte: float = Field(1.0, gt=0, le=1, description="Full-time equivalent, 1.0 = full time")

    @model_validator(mode="after")
    def _order(self):
        if self.as_of < self.start_date:
            raise ValueError("as_of must be on or after start_date")
        return self


@rule(
    "annual_leave_balance",
    "Annual leave balance",
    LeaveBalanceInput,
    "annual_leave",
    keywords=("leave balance", "pto", "vacation", "holiday", "days off", "annual leave", "days left", "how many days"),
)
def annual_leave_balance(i: LeaveBalanceInput, p: dict) -> RuleOutcome:
    """Compute an employee's current annual leave (PTO) balance in days: monthly accrual
    including seniority bonus and FTE proration, valid carryover, minus days taken."""
    steps: list[str] = []
    accrual_start = i.start_date if i.start_date.day == 1 else _month_end(i.start_date) + timedelta(days=1)
    steps.append(f"Accrual starts on {accrual_start} (joiners mid-month accrue from the 1st of next month).")

    accrued = 0.0
    months = 0
    last_bonus = None
    for m in range(1, 13):
        first = date(i.as_of.year, m, 1)
        if first < accrual_start or _month_end(first) > i.as_of:
            continue
        years = _full_years(i.start_date, first)
        bonus = min(years // p["seniority_bonus_every_years"], p["seniority_bonus_max_days"])
        yearly = (p["days_per_year_full_time"] + bonus) * i.fte
        accrued += yearly / 12
        months += 1
        if bonus != last_bonus:
            last_bonus = bonus
            steps.append(
                f"From {first:%B}: entitlement ({p['days_per_year_full_time']} + {bonus} seniority) x {i.fte} FTE "
                f"= {yearly:.2f} days/year, {yearly / 12:.4f} per month."
            )
    steps.append(f"{months} completed month(s) in {i.as_of.year} -> {accrued:.2f} days accrued.")

    carry = min(i.carryover_days, p["carryover_cap_days"])
    warnings = []
    if i.carryover_days > p["carryover_cap_days"]:
        warnings.append(f"Carryover capped at {p['carryover_cap_days']} days; the excess was forfeited.")
    if i.as_of > date(i.as_of.year, 3, 31) and carry:
        warnings.append("Carried-over days expired on 31 March and are not counted.")
        carry = 0.0
    steps.append(f"Valid carryover: {carry:.2f} days.")

    balance = round(accrued + carry - i.days_taken, 2)
    steps.append(f"Balance = {accrued:.2f} + {carry:.2f} - {i.days_taken:.2f} taken = {balance:.2f} days.")
    if balance < 0:
        warnings.append("Negative balance: more leave taken than accrued so far.")
    return RuleOutcome(
        answer=f"Your annual leave balance on {i.as_of} is {balance:.2f} days.",
        result={"balance_days": balance, "accrued_days": round(accrued, 2), "carryover_days": carry, "months_accrued": months},
        steps=steps,
        citations=[],
        warnings=warnings,
    )


# --------------------------------------------------------------------------- notice period


class NoticeInput(BaseModel):
    level: Literal["individual_contributor", "manager", "executive"] = Field(
        description="Job level: individual_contributor, manager or executive"
    )
    start_date: date = Field(description="Employment start date (YYYY-MM-DD)")
    as_of: date = Field(default_factory=date.today, description="Date notice is given (YYYY-MM-DD)")
    contract_notice_months: float | None = Field(
        None, ge=0, description="Notice period written in the individual contract, if any"
    )


@rule(
    "notice_period",
    "Notice period",
    NoticeInput,
    "notice_period",
    keywords=("notice period", "notice", "resign", "quit", "leave the company", "terminate", "fired"),
)
def notice_period(i: NoticeInput, p: dict) -> RuleOutcome:
    """Determine the notice period owed when an employee resigns or is dismissed, from job
    level, length of service, probation status and any longer contractual notice."""
    steps = []
    probation_end = _add_months(i.start_date, p["probation_months"][i.level])
    if i.as_of < probation_end:
        weeks = p["probation_notice_weeks"]
        steps.append(f"Probation ({p['probation_months'][i.level]} months) runs until {probation_end}; still on probation.")
        steps.append(f"Notice during probation is {weeks} weeks.")
        return RuleOutcome(
            answer=f"You are still on probation (until {probation_end}), so the notice period is {weeks} weeks.",
            result={"notice_weeks": weeks, "notice_months": None, "on_probation": True},
            steps=steps,
            citations=[{"source": "02-employment-and-termination.md", "section": "Employment Terms and Termination > Probation Period"}],
        )

    tenure = (i.as_of - i.start_date).days / 365.25
    bucket = 0 if tenure < 2 else (1 if tenure <= 5 else 2)
    labels = ["less than 2 years", "2 to 5 years", "more than 5 years"]
    months = p["months_by_level"][i.level][bucket]
    steps.append(f"Probation ended on {probation_end}.")
    steps.append(f"Service: {tenure:.2f} years ({labels[bucket]}); level: {i.level.replace('_', ' ')}.")
    steps.append(f"Policy table -> {months} month(s).")
    if i.contract_notice_months and i.contract_notice_months > months:
        steps.append(f"Contract states {i.contract_notice_months:g} months, which is longer and prevails.")
        months = i.contract_notice_months
    return RuleOutcome(
        answer=f"Your notice period is {months:g} month{'s' if months != 1 else ''}.",
        result={"notice_months": months, "notice_weeks": None, "on_probation": False, "tenure_years": round(tenure, 2)},
        steps=steps,
        citations=[],
    )


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


# --------------------------------------------------------------------------- overtime


class OvertimeInput(BaseModel):
    hourly_rate: float = Field(gt=0, description="Base hourly rate in USD")
    hours_worked: float = Field(ge=0, le=168, description="Hours worked in the week, excluding public holiday hours")
    public_holiday_hours: float = Field(0, ge=0, description="Hours worked on a public holiday that week")
    exempt: bool = Field(False, description="True for salaried/exempt employees (managers, executives)")


@rule(
    "overtime_pay",
    "Weekly overtime pay",
    OvertimeInput,
    "overtime",
    keywords=("overtime", "extra hours", "hours this week", "worked", "public holiday", "per hour", "hourly"),
)
def overtime_pay(i: OvertimeInput, p: dict) -> RuleOutcome:
    """Calculate weekly pay including overtime premiums (125% from 40h, 150% from 48h, 200% on
    public holidays) for an hourly, non-exempt employee, and flag working-time violations."""
    r = i.hourly_rate
    std, t1 = p["standard_weekly_hours"], p["tier1_until_hours"]
    regular_h = min(i.hours_worked, std)
    t1_h = max(0.0, min(i.hours_worked, t1) - std)
    t2_h = max(0.0, i.hours_worked - t1)
    warnings = []
    total_h = i.hours_worked + i.public_holiday_hours
    if total_h > p["max_hours_single_week"]:
        warnings.append(f"{total_h:g} hours exceeds the {p['max_hours_single_week']}-hour weekly maximum: compliance breach.")

    if i.exempt:
        return RuleOutcome(
            answer="Exempt (salaried) employees are not paid overtime; extra hours are compensated with time off in lieu agreed with the manager.",
            result={"overtime_pay": 0.0, "exempt": True},
            steps=["Employee is exempt -> overtime premiums do not apply."],
            citations=[{"source": "03-working-time-and-overtime.md", "section": "Working Time and Overtime > Overtime Eligibility"}],
            warnings=warnings,
        )

    regular = regular_h * r
    tier1 = t1_h * r * p["tier1_multiplier"]
    tier2 = t2_h * r * p["tier2_multiplier"]
    holiday = i.public_holiday_hours * r * p["public_holiday_multiplier"]
    overtime = tier1 + tier2 + holiday
    total = regular + overtime
    steps = [
        f"Regular: {regular_h:g} h x {_money(r)} = {_money(regular)}",
        f"40-48 h band: {t1_h:g} h x {_money(r)} x {p['tier1_multiplier']} = {_money(tier1)}",
        f"Above 48 h: {t2_h:g} h x {_money(r)} x {p['tier2_multiplier']} = {_money(tier2)}",
        f"Public holiday: {i.public_holiday_hours:g} h x {_money(r)} x {p['public_holiday_multiplier']} = {_money(holiday)}",
        f"Total = {_money(total)} (of which overtime and holiday hours {_money(overtime)})",
    ]
    return RuleOutcome(
        answer=f"Total pay for the week is {_money(total)}, including {_money(overtime)} for overtime and holiday hours.",
        result={"total_pay": round(total, 2), "overtime_pay": round(overtime, 2), "regular_pay": round(regular, 2),
                "hours": {"regular": regular_h, "tier1": t1_h, "tier2": t2_h, "holiday": i.public_holiday_hours}},
        steps=steps,
        citations=[],
        warnings=warnings,
    )


# --------------------------------------------------------------------------- expenses


class ExpenseInput(BaseModel):
    category: Literal["meals", "hotel", "ground_transport", "client_entertainment", "other"] = Field(
        description="Expense category"
    )
    amount: float = Field(gt=0, description="Total amount in USD")
    city: str | None = Field(None, description="City where the expense was incurred")
    has_receipt: bool = Field(True, description="Whether an itemised receipt is attached")
    pre_approved: bool = Field(False, description="Whether the manager pre-approved it in writing")
    units: int = Field(1, ge=1, description="Number of days (meals, transport) or nights (hotel) covered")
    attendees: int = Field(1, ge=1, description="Attendees incl. the employee, for client entertainment")
    days_since_expense: int = Field(0, ge=0, description="Days between the expense and its submission")


@rule(
    "expense_check",
    "Expense reimbursement check",
    ExpenseInput,
    "expenses",
    keywords=("expense", "reimburse", "receipt", "hotel", "meal", "dinner", "lunch", "taxi", "uber", "per night", "claim"),
)
def expense_check(i: ExpenseInput, p: dict) -> RuleOutcome:
    """Decide whether an expense is reimbursable and for how much: receipts, pre-approval
    threshold, daily limits by city tier, client entertainment caps, submission deadline."""
    steps, reasons, warnings = [], [], []
    tier = "tier1" if i.city and i.city.strip().lower() in p["tier1_cities"] else "tier2"
    if i.city:
        steps.append(f"{i.city} is a {'Tier 1' if tier == 'tier1' else 'Tier 2'} city.")

    if i.days_since_expense > p["submission_deadline_days"]:
        return _expense(
            "rejected", 0.0, steps + [f"Submitted {i.days_since_expense} days later (> {p['submission_deadline_days']})."],
            [f"Expenses submitted after {p['submission_deadline_days']} days are not reimbursed."], warnings,
        )
    if i.days_since_expense > 30:
        warnings.append("Submitted after the 30-day guideline; still reimbursable but flagged.")
    if i.amount >= p["receipt_required_from"] and not i.has_receipt:
        return _expense(
            "rejected", 0.0, steps + [f"{_money(i.amount)} >= {_money(p['receipt_required_from'])} and no receipt."],
            ["An itemised receipt is required for expenses of 25 USD or more."], warnings,
        )
    if i.amount > p["pre_approval_above"] and not i.pre_approved:
        return _expense(
            "needs_pre_approval", 0.0, steps + [f"{_money(i.amount)} > {_money(p['pre_approval_above'])} without pre-approval."],
            ["Single expenses above 1,000 USD require written manager pre-approval before they are incurred."], warnings,
        )

    cap = None
    if i.category in p["limits"]:
        per_unit = p["limits"][i.category][tier]
        cap = per_unit * i.units
        steps.append(f"Limit: {_money(per_unit)} x {i.units} = {_money(cap)}.")
    elif i.category == "client_entertainment":
        cap = p["client_entertainment_per_attendee"] * i.attendees
        steps.append(f"Limit: {_money(p['client_entertainment_per_attendee'])} x {i.attendees} attendee(s) = {_money(cap)}.")
        warnings.append("List the names and companies of all attendees in the expense report.")

    if cap is not None and i.amount > cap and not i.pre_approved:
        reasons.append(f"Amount above the limit; only {_money(cap)} is reimbursed (excess needs written pre-approval).")
        steps.append(f"{_money(i.amount)} > {_money(cap)} -> capped.")
        return _expense("partially_approved", cap, steps, reasons, warnings)
    steps.append("Within policy.")
    return _expense("approved", i.amount, steps, reasons, warnings)


def _expense(status, amount, steps, reasons, warnings) -> RuleOutcome:
    verdict = {
        "approved": f"Approved: {_money(amount)} is reimbursable.",
        "partially_approved": f"Partially approved: {_money(amount)} is reimbursable.",
        "needs_pre_approval": "Not reimbursable as submitted: it needs written manager pre-approval.",
        "rejected": "Rejected: this expense is not reimbursable.",
    }[status]
    return RuleOutcome(
        answer=" ".join([verdict, *reasons]),
        result={"status": status, "reimbursable_amount": round(amount, 2)},
        steps=steps,
        citations=[{"source": "04-travel-and-expenses.md", "section": "Travel and Expense Policy > Receipts"}],
        warnings=warnings,
    )


# --------------------------------------------------------------------------- remote work


class RemoteInput(BaseModel):
    role: str = Field(description="Job title, e.g. 'backend developer' or 'lab technician'")
    probation_completed: bool = Field(description="Whether the probation period is completed")
    performance_rating: Literal["below", "meets", "exceeds"] = Field(
        "meets", description="Most recent performance rating"
    )
    on_pip: bool = Field(False, description="Whether the employee is on a performance improvement plan")


@rule(
    "remote_work_eligibility",
    "Remote work eligibility",
    RemoteInput,
    "remote_work",
    keywords=("remote", "work from home", "wfh", "home office", "telework", "days from home"),
)
def remote_work_eligibility(i: RemoteInput, p: dict) -> RuleOutcome:
    """Decide whether an employee may work remotely and how many days per week, from role,
    probation status, performance rating and performance-plan status."""
    role = i.role.lower()
    steps, blockers = [], []
    if any(r in role for r in p["onsite_roles"]):
        blockers.append(f"'{i.role}' is an on-site role.")
    if not i.probation_completed:
        blockers.append("Probation is not completed.")
    if i.performance_rating == "below":
        blockers.append("Most recent rating is below 'meets expectations'.")
    if i.on_pip:
        blockers.append("Employee is on a performance improvement plan.")
    steps.extend(f"Blocker: {b}" for b in blockers)
    if blockers:
        return RuleOutcome(
            answer="Not eligible for remote work: " + " ".join(blockers),
            result={"eligible": False, "max_remote_days_per_week": 0, "blockers": blockers},
            steps=steps,
            citations=[],
        )
    family = next((f for f, kws in p["remote_first_roles"].items() if any(k in role for k in kws)), None)
    days = p["max_days_remote_first"] if family else p["max_days_standard"]
    steps.append(f"Role family: {family or 'standard'} -> up to {days} remote days per week.")
    extra = " You must attend the quarterly on-site planning week." if family else ""
    return RuleOutcome(
        answer=f"Eligible for remote work up to {days} days per week.{extra}",
        result={"eligible": True, "max_remote_days_per_week": days, "remote_first_family": family},
        steps=steps,
        citations=[{"source": "05-remote-work.md", "section": "Remote Work Policy > Number of Remote Days"}],
    )
