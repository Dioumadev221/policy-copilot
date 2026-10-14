"""Question -> route -> (rule | RAG | refuse | ask for missing input) -> logged answer."""

import json
import logging
import re
import time
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.db import pool
from app.llm.providers import ToolSpec, get_llm
from app.retrieval.search import Passage, search
from app.retrieval.text import terms
from app.rules import REGISTRY, get_rule, params
from app.rules.extract import heuristic_route

log = logging.getLogger(__name__)
SEARCH_TOOL = "search_policy_documents"

ROUTER_SYSTEM = """You route employee questions for Helios Robotics' HR policy assistant. Today is {today}.
Call a calculation tool only when the user asks for a calculation or decision about a specific
situation (their own or a described case): a leave balance, a notice period, an overtime payout,
whether an expense is reimbursable, remote-work eligibility. Fill only the arguments the user
actually stated or that follow unambiguously from what they said; leave everything else out,
never guess values. For general questions about what a policy says, call {search}."""

ANSWER_SYSTEM = """You answer questions about Helios Robotics' internal policies using ONLY the
numbered passages provided. Cite every factual sentence with the passage number in square
brackets, like [1] or [2][3]. If the passages do not contain the answer, reply exactly:
"I can't find this in the policy documents." Be concise: 1 to 4 sentences, no preamble."""


class Citation(BaseModel):
    n: int
    source: str
    title: str
    section: str
    snippet: str
    relevance: float | None = None


class RuleTrace(BaseModel):
    name: str
    title: str
    params_version: str
    inputs: dict[str, Any]
    assumptions: list[str]
    result: dict[str, Any]
    steps: list[str]
    warnings: list[str]


class AskResponse(BaseModel):
    route: Literal["rule", "rag", "refused", "needs_input"]
    answer: str
    guaranteed: bool  # True only when the answer was computed by the rules engine
    grounded: bool  # every claim is backed by a citation
    rule: RuleTrace | None = None
    missing_fields: list[str] = []
    citations: list[Citation] = []
    provider: str
    latency_ms: int


# --------------------------------------------------------------------------- routing


def _tools() -> list[ToolSpec]:
    specs = [ToolSpec(r.name, r.description, r.schema()) for r in REGISTRY.values()]
    specs.append(
        ToolSpec(
            SEARCH_TOOL,
            "Search the HR, travel/expense, remote work and code of conduct policies to answer a general policy question.",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        )
    )
    return specs


def _route(question: str) -> tuple[str, dict[str, Any]] | None:
    llm = get_llm()
    if llm is None:
        return heuristic_route(question)
    call = llm.route(ROUTER_SYSTEM.format(today=date.today(), search=SEARCH_TOOL), question, _tools())
    if call is None or call.name == SEARCH_TOOL or call.name not in REGISTRY:
        return None
    return call.name, {k: v for k, v in call.arguments.items() if v is not None}


# --------------------------------------------------------------------------- rule path


def _section_citations(cites: list[dict[str, str]]) -> list[Citation]:
    out: list[Citation] = []
    with pool().connection() as conn:
        for c in cites:
            row = conn.execute(
                """SELECT d.title, c.section_path, c.content FROM chunks c JOIN documents d ON d.id = c.document_id
                   WHERE d.source = %s AND c.section_path LIKE %s ORDER BY c.ord LIMIT 1""",
                (c["source"], c["section"] + "%"),
            ).fetchone()
            if row and not any(o.section == row["section_path"] for o in out):
                out.append(Citation(n=len(out) + 1, source=c["source"], title=row["title"],
                                    section=row["section_path"], snippet=row["content"][:400]))
    return out


def _run_rule(name: str, args: dict[str, Any]) -> AskResponse | dict:
    r = get_rule(name)
    missing = r.missing_fields(args)
    if missing:
        fields = r.input_model.model_fields
        asks = "; ".join(f"{f} ({fields[f].description})" for f in missing)
        return {"route": "needs_input", "missing": missing,
                "answer": f"I can calculate this exactly ({r.title.lower()}), but I need: {asks}."}
    try:
        outcome = r.evaluate(args)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
        return {"route": "needs_input", "missing": [], "answer": f"I can't run the {r.title.lower()} calculation: {problems}."}
    validated = r.input_model.model_validate(args)
    assumptions = [
        f"{f} = {getattr(validated, f)} (not stated, policy default used)"
        for f in r.input_model.model_fields
        if f not in args
    ]
    return {"route": "rule", "outcome": outcome, "trace": RuleTrace(
        name=r.name, title=r.title, params_version=params()["version"],
        inputs=json.loads(validated.model_dump_json()), assumptions=assumptions, result=outcome.result,
        steps=outcome.steps, warnings=outcome.warnings)}


# --------------------------------------------------------------------------- RAG path

_SENT = re.compile(r"(?<=[.!?])\s+")
_CITE = re.compile(r"\[(\d+)\]")


def _context(passages: list[Passage]) -> str:
    return "\n\n".join(f"[{i}] ({p.title} > {p.section_path.split(' > ', 1)[-1]})\n{p.content}"
                       for i, p in enumerate(passages, start=1))


def _units(content: str) -> list[str]:
    """Sentences, plus table rows rendered as 'Row: Col A value, Col B value'."""
    out: list[str] = []
    for block in content.split("\n\n"):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines and lines[0].startswith("|"):
            header = [c.strip() for c in lines[0].strip("|").split("|")]
            for row in lines[2:]:
                cells = [c.strip() for c in row.strip("|").split("|")]
                out.append(f"{cells[0]}: " + ", ".join(f"{h} {v}" for h, v in zip(header[1:], cells[1:])) + ".")
        else:
            out.extend(s.strip() for s in _SENT.split(" ".join(lines)) if s.strip())
    return out


def _extractive(question: str, passages: list[Passage]) -> str:
    """Offline generator: the best-matching sentences or table rows, verbatim, each cited.

    Only passages close to the top score are used, so a strong first hit is not diluted
    by loosely related neighbours.
    """
    q = set(terms(question))
    top = passages[0].relevance
    scored = []
    for i, p in enumerate(passages[:3], start=1):
        if p.relevance < 0.8 * top:
            continue
        section_terms = set(terms(p.section_path))
        for j, unit in enumerate(_units(p.content)):
            overlap = len(q & set(terms(unit)))
            if overlap:
                bonus = 0.5 * len(q & section_terms)
                scored.append(((overlap + bonus) * p.relevance, i, j, unit))
    best = sorted(scored, reverse=True)[:2]
    best.sort(key=lambda x: (x[1], x[2]))
    return " ".join(f"{s} [{i}]" for _, i, _, s in best)


def _verify_citations(answer: str, n_passages: int) -> tuple[str, bool]:
    """Drop citations that point at nothing; grounded only if at least one valid citation remains."""
    cited = [int(x) for x in _CITE.findall(answer)]
    invalid = {c for c in cited if not 1 <= c <= n_passages}
    for c in invalid:
        answer = answer.replace(f"[{c}]", "")
    return answer.strip(), any(1 <= c <= n_passages for c in cited)


def _rag(question: str) -> tuple[str, str, bool, list[Passage]]:
    s = get_settings()
    passages = search(question)
    if not passages or passages[0].relevance < s.min_relevance:
        return "refused", "I can't find this in the policy documents. Please contact People Operations.", False, []
    llm = get_llm()
    if llm is None:
        answer = _extractive(question, passages)
    else:
        answer = llm.generate(ANSWER_SYSTEM, f"Passages:\n{_context(passages)}\n\nQuestion: {question}")
        if "can't find this in the policy documents" in answer.lower():
            return "refused", answer, False, []
    answer, grounded = _verify_citations(answer, len(passages))
    if not grounded:  # the model answered without evidence: do not show an unsupported claim
        answer, grounded = _verify_citations(_extractive(question, passages), len(passages))
    if not answer:
        return "refused", "I can't find this in the policy documents. Please contact People Operations.", False, []
    return "rag", answer, grounded, passages


# --------------------------------------------------------------------------- entry point


def ask(question: str) -> AskResponse:
    t0 = time.perf_counter()
    llm = get_llm()
    provider = llm.name if llm else "local"
    resp: dict[str, Any] = {"provider": provider, "guaranteed": False, "grounded": False}

    routed = _route(question)
    if routed:
        res = _run_rule(*routed)
        if res["route"] == "rule":
            outcome, trace = res["outcome"], res["trace"]
            answer = outcome.answer
            if outcome.warnings:
                answer += " Note: " + " ".join(outcome.warnings)
            resp.update(route="rule", answer=answer, guaranteed=True, grounded=True, rule=trace,
                        citations=_section_citations(outcome.citations))
        else:
            resp.update(route="needs_input", answer=res["answer"], missing_fields=res["missing"])
    else:
        route, answer, grounded, passages = _rag(question)
        resp.update(route=route, answer=answer, grounded=grounded, citations=[
            Citation(n=i, source=p.source, title=p.title, section=p.section_path, snippet=p.content[:400],
                     relevance=p.relevance)
            for i, p in enumerate(passages, start=1)
        ])

    resp["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    out = AskResponse(**resp)
    _log(question, out)
    return out


def _log(question: str, r: AskResponse) -> None:
    try:
        with pool().connection() as conn:
            conn.execute(
                "INSERT INTO query_log (question, route, rule_name, answer, sources, latency_ms) VALUES (%s,%s,%s,%s,%s,%s)",
                (question, r.route, r.rule.name if r.rule else None, r.answer,
                 json.dumps([{"source": c.source, "section": c.section} for c in r.citations]), r.latency_ms),
            )
    except Exception:  # logging must never break an answer
        log.exception("failed to write query_log")
