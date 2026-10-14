"""Offline router: keyword intent detection + regex argument extraction.

Used when no LLM key is configured (CI, demo). With an LLM configured, routing and
extraction are done by tool calling, and these extractors are not used. Either way the
rules engine re-validates every argument, so a bad extraction fails loudly instead of
producing a wrong number.
"""

import re
from datetime import date
from typing import Any

from app.rules.engine import REGISTRY, params

NUM = r"(\d+(?:[.,]\d+)?)"
_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), start=1)}
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", re.I),
     lambda m: date(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))),
    (re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),?\s+(\d{4})\b", re.I),
     lambda m: date(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))),
]
_START_CUE = re.compile(r"(start|join|hired|employed since|since)\w*\s+(?:on\s+|in\s+)?$", re.I)
_ASOF_CUE = re.compile(r"(as of|today is|by|until|end of|resign on|resigning on|notice on)\s+$", re.I)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _dates(q: str) -> list[tuple[int, date, str]]:
    found = []
    for rx, conv in _DATE_PATTERNS:
        for m in rx.finditer(q):
            try:
                found.append((m.start(), conv(m), q[max(0, m.start() - 30) : m.start()]))
            except ValueError:
                continue
    return sorted(found, key=lambda x: x[0])


def _start_and_asof(q: str, out: dict[str, Any]) -> None:
    for _, d, before in _dates(q):
        if "start_date" not in out and _START_CUE.search(before):
            out["start_date"] = d
        elif "as_of" not in out and _ASOF_CUE.search(before):
            out["as_of"] = d
        elif "start_date" not in out:
            out["start_date"] = d
        elif "as_of" not in out:
            out["as_of"] = d


def _first(rx: str, q: str, flags=re.I) -> re.Match | None:
    return re.search(rx, q, flags)


def extract_leave(q: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    _start_and_asof(q, out)
    if m := _first(rf"(?:took|taken|used|have used|have taken)\s+{NUM}\s*(?:days?)", q) or _first(
        rf"{NUM}\s*days?\s+(?:taken|off|used|already)", q
    ):
        out["days_taken"] = _num(m[1])
    if m := _first(rf"{NUM}\s*days?\s+(?:carried|of carryover|carryover|carry[- ]over|from last year)", q):
        out["carryover_days"] = _num(m[1])
    if m := _first(rf"{NUM}\s*fte", q):
        out["fte"] = _num(m[1])
    elif m := _first(r"(\d{2,3})\s*%\s*(?:part[- ]time|time|fte|schedule)|part[- ]time (?:at|on)\s*(\d{2,3})\s*%", q):
        out["fte"] = int(m[1] or m[2]) / 100
    return out


def extract_notice(q: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    _start_and_asof(q, out)
    ql = q.lower()
    if re.search(r"\b(executive|director|vp|vice president|c-level|cto|ceo|cfo|coo)\b", ql):
        out["level"] = "executive"
    elif re.search(r"\b(manager|team lead|head of)\b", ql):
        out["level"] = "manager"
    elif re.search(r"\b(engineer|developer|analyst|designer|individual contributor|ic|employee|specialist|technician)\b", ql):
        out["level"] = "individual_contributor"
    if m := _first(rf"contract\s+(?:says|states|specifies|has|includes)\s+(?:a\s+)?{NUM}[- ]months?", q):
        out["contract_notice_months"] = _num(m[1])
    return out


def extract_overtime(q: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if m := _first(rf"(?:\$|usd\s*){NUM}\s*(?:/|per|an|a)\s*h(?:ou)?r", q) or _first(
        rf"{NUM}\s*(?:\$|usd|dollars)\s*(?:/|per|an|a)\s*h(?:ou)?r", q
    ) or _first(rf"hourly rate (?:is|of)\s*(?:\$|usd\s*)?{NUM}", q):
        out["hourly_rate"] = _num(m[1])
    holiday = _first(rf"{NUM}\s*(?:hours?|h)\b[^.,;\d]{{0,25}}?(?:on|during)\s+(?:a\s+|the\s+)?(?:public\s+|bank\s+)?holiday", q)
    if holiday:
        out["public_holiday_hours"] = _num(holiday[1])
    for m in re.finditer(rf"{NUM}\s*(?:hours?|h)\b", q, re.I):
        if holiday and m.start() == holiday.start():
            continue
        out["hours_worked"] = _num(m[1])
        break
    if re.search(r"\b(salaried|exempt)\b", q, re.I) and not re.search(r"\bnon[- ]exempt\b", q, re.I):
        out["exempt"] = True
    return out


def extract_expense(q: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ql = q.lower()
    if re.search(r"client|customer", ql) and re.search(r"dinner|lunch|meal|entertain|drinks", ql):
        out["category"] = "client_entertainment"
    elif re.search(r"hotel|night", ql):
        out["category"] = "hotel"
    elif re.search(r"meal|dinner|lunch|breakfast|food", ql):
        out["category"] = "meals"
    elif re.search(r"taxi|uber|lyft|cab|ground transport|metro|bus", ql):
        out["category"] = "ground_transport"
    if m := _first(rf"(?:\$|usd\s*){NUM}", q) or _first(rf"{NUM}\s*(?:\$|usd|dollars?)", q):
        out["amount"] = _num(m[1])
    for city in params()["expenses"]["tier1_cities"]:
        if city in ql:
            out["city"] = city.title()
            break
    else:
        if m := re.search(r"\bin ([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?)", q):
            out["city"] = m[1]
    if m := _first(r"(\d+)\s*nights?", q) or _first(r"(\d+)[- ]day\b", q) or _first(r"for (\d+)\s*days\b", q):
        out["units"] = int(m[1])
    if re.search(r"no receipt|without (?:a |any )?receipt|lost (?:the|my) receipt|don'?t have (?:a |the )?receipt", ql):
        out["has_receipt"] = False
    elif "receipt" in ql:
        out["has_receipt"] = True
    if re.search(r"not (?:been )?pre-?approved|without (?:pre-?)?approval|no (?:pre-?)?approval", ql):
        out["pre_approved"] = False
    elif re.search(r"pre-?approved|approved in advance", ql):
        out["pre_approved"] = True
    if m := _first(r"(\d+)\s*(?:people|attendees|guests|persons|of us)", q):
        out["attendees"] = int(m[1])
    if m := _first(r"(\d+)\s*days?\s+ago", q):
        out["days_since_expense"] = int(m[1])
    return out


def extract_remote(q: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ql = q.lower()
    if m := re.search(r"(?:i am|i'm|as|work as|my role is|my job is)\s+an?\s+([a-z][a-z \-]+?)(?=[,.;]| and | who | with | on | in | at |$)", ql):
        out["role"] = m[1].strip()
    if re.search(r"(still|currently) (?:on|in) probation|haven'?t (?:finished|completed|passed) (?:my )?probation|during (?:my )?probation", ql):
        out["probation_completed"] = False
    elif re.search(r"(passed|completed|finished|after|past|out of) (?:my |the )?probation", ql):
        out["probation_completed"] = True
    elif m := re.search(r"(?:joined|started|been here)\D{0,20}(\d+)\s*(months?|years?)", ql):
        months = int(m[1]) * (12 if m[2].startswith("year") else 1)
        out["probation_completed"] = months >= 3
    if re.search(r"exceed", ql):
        out["performance_rating"] = "exceeds"
    elif re.search(r"below expectations|did not meet|didn'?t meet|needs improvement", ql):
        out["performance_rating"] = "below"
    elif re.search(r"meets expectations|met expectations", ql):
        out["performance_rating"] = "meets"
    if re.search(r"\bpip\b|performance improvement plan", ql):
        out["on_pip"] = not re.search(r"not on (?:a |any )?(?:pip|performance improvement plan)", ql)
    return out


EXTRACTORS = {
    "annual_leave_balance": extract_leave,
    "notice_period": extract_notice,
    "overtime_pay": extract_overtime,
    "expense_check": extract_expense,
    "remote_work_eligibility": extract_remote,
}

# Words signalling the user wants a decision about *their* case, not a policy explanation.
_PERSONAL = re.compile(r"\b(i|i'm|i've|my|me|we|our)\b|\bcan (?:i|she|he|they)\b", re.I)


def heuristic_route(question: str) -> tuple[str, dict[str, Any]] | None:
    """Return (rule_name, extracted_args) or None to fall through to document search."""
    if not _PERSONAL.search(question):
        return None
    ql = question.lower()
    scored = sorted(
        ((sum(1 for kw in r.keywords if kw in ql), name) for name, r in REGISTRY.items()), reverse=True
    )
    for score, name in scored:
        if score == 0:
            break
        # A personal question with no concrete facts for this rule is not a calculation.
        if args := EXTRACTORS[name](question):
            return name, args
    return None
