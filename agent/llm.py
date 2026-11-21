"""Pluggable LLM provider for the agent engine.

The system is fully functional without an external LLM: the offline provider
returns empty structured output and the investigator's evidence-driven rule
engine performs the reasoning.  When an OpenAI-compatible endpoint is
configured, the provider is used to enrich hypotheses and fix recommendations.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from config.settings import settings


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    duration: float
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class LLMProvider(ABC):
    name = "base"

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 2000) -> LLMResponse:
        raise NotImplementedError


class OfflineProvider(LLMProvider):
    """Zero-dependency provider; reasoning is handled by local rules."""

    name = "offline-rule-engine"

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> LLMResponse:
        return LLMResponse(text="", provider=self.name, model="offline", duration=0.0)


class OpenAICompatibleProvider(LLMProvider):
    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> LLMResponse:
        url = self.base_url
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        payload = json.dumps({
            "model": self.model,
            "temperature": settings.llm_temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        start = time.time()
        try:
            with urllib.request.urlopen(request, timeout=settings.command_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"]
            return LLMResponse(
                text=text, provider=self.name, model=self.model,
                duration=round(time.time() - start, 3),
            )
        except Exception as exc:  # network/JSON errors must not abort analysis
            return LLMResponse(
                text="", provider=self.name, model=self.model,
                duration=round(time.time() - start, 3), error=str(exc)[:400],
            )


def get_provider() -> LLMProvider:
    if settings.llm_configured:
        return OpenAICompatibleProvider(
            settings.llm_base_url, settings.llm_api_key, settings.llm_model
        )
    return OfflineProvider()


def parse_json_response(text: str) -> Optional[Any]:
    """Extract the first JSON object/array from an LLM response."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        if start == -1:
            continue
        end = cleaned.rfind(closer)
        if end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None
