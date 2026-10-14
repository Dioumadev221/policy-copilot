"""Deterministic rules engine.

Some answers must be correct every single time: a leave balance, a notice period, an
overtime payout, whether an expense is reimbursable. An LLM may *route* the question and
*extract* the inputs, but it never computes the number. Rules are plain Python over
typed inputs and versioned parameters, return a step-by-step trace, and cite the policy
section they implement. Same input, same output, auditable.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

PARAMS_PATH = Path(__file__).with_name("policy_params.yaml")


@lru_cache
def params() -> dict[str, Any]:
    return yaml.safe_load(PARAMS_PATH.read_text())


@dataclass
class RuleOutcome:
    answer: str
    result: dict[str, Any]
    steps: list[str]
    citations: list[dict[str, str]]
    warnings: list[str] = field(default_factory=list)


@dataclass
class Rule:
    name: str
    title: str
    description: str  # also used as the tool description for LLM routing
    input_model: type[BaseModel]
    fn: Callable[[Any, dict[str, Any]], RuleOutcome]
    params_key: str
    keywords: tuple[str, ...]  # for the offline router

    def evaluate(self, raw: dict[str, Any]) -> RuleOutcome:
        inputs = self.input_model.model_validate(raw)
        p = params()[self.params_key]
        outcome = self.fn(inputs, p)
        outcome.citations = [{"source": p["source"], "section": p["section"]}] + outcome.citations
        return outcome

    def missing_fields(self, raw: dict[str, Any]) -> list[str]:
        try:
            self.input_model.model_validate(raw)
            return []
        except ValidationError as exc:
            return sorted({str(e["loc"][0]) for e in exc.errors() if e["type"] == "missing"})

    def schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()


REGISTRY: dict[str, Rule] = {}


def rule(name: str, title: str, input_model: type[BaseModel], params_key: str, keywords: tuple[str, ...]):
    def deco(fn):
        REGISTRY[name] = Rule(
            name=name,
            title=title,
            description=(fn.__doc__ or "").strip(),
            input_model=input_model,
            fn=fn,
            params_key=params_key,
            keywords=keywords,
        )
        return fn

    return deco


def get_rule(name: str) -> Rule:
    if name not in REGISTRY:
        raise KeyError(name)
    return REGISTRY[name]
