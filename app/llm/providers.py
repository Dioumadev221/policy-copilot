"""LLM adapters. The LLM has exactly two jobs here:

1. route(): pick a rule and extract its typed arguments (tool calling), or decline so the
   question goes to document search;
2. generate(): write an answer grounded in retrieved passages, with [n] citations.

It never computes a guaranteed number. Swapping providers is a config change.
"""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


class LLM(Protocol):
    name: str

    def route(self, system: str, question: str, tools: list[ToolSpec]) -> ToolCall | None: ...

    def generate(self, system: str, prompt: str) -> str: ...


class OpenAILLM:
    name = "openai"

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, max_retries=3, timeout=45)
        self.model = model

    def route(self, system: str, question: str, tools: list[ToolSpec]) -> ToolCall | None:
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": question}],
            tools=[
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ],
            tool_choice="auto",
            parallel_tool_calls=False,
        )
        calls = resp.choices[0].message.tool_calls or []
        if not calls:
            return None
        return ToolCall(calls[0].function.name, json.loads(calls[0].function.arguments or "{}"))

    def generate(self, system: str, prompt: str) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key, max_retries=3, timeout=60)
        self.model = model

    def _create(self, effort: str, **kwargs):
        # Server-side fallbacks: if the model declines, the API retries on a substitute model.
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **kwargs,
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("The model declined this request.")
        return resp

    def route(self, system: str, question: str, tools: list[ToolSpec]) -> ToolCall | None:
        resp = self._create(
            "low",
            system=system,
            messages=[{"role": "user", "content": question}],
            tools=[{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools],
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        )
        for block in resp.content:
            if block.type == "tool_use":
                return ToolCall(block.name, dict(block.input))
        return None

    def generate(self, system: str, prompt: str) -> str:
        resp = self._create("medium", system=system, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in resp.content if b.type == "text")


@lru_cache
def get_llm() -> LLM | None:
    """None means offline mode: heuristic router + extractive answers."""
    s = get_settings()
    if s.llm_provider == "openai":
        if not s.openai_api_key:
            raise RuntimeError("LLM_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAILLM(s.openai_api_key, s.openai_chat_model)
    if s.llm_provider == "anthropic":
        if not s.anthropic_api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        return AnthropicLLM(s.anthropic_api_key, s.anthropic_chat_model)
    return None
