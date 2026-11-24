"""Optional AI provider abstraction.

The platform works fully without AI. When a provider is configured it is only used to
re-word answers that the deterministic engine has already computed — it receives the
computed facts (never uploaded files) and its output is labelled as AI-written.

Supported out of the box (all optional):
  AI_PROVIDER=none            (default) no AI
  AI_PROVIDER=openai_compatible  any OpenAI-compatible chat endpoint: a free local model via
                              Ollama (AI_BASE_URL=http://localhost:11434/v1) or LM Studio, or a
                              hosted API with your own key. Claude/Gemini can be added as new classes.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from abc import ABC, abstractmethod

from ..config import settings

log = logging.getLogger("dip.ai")

SYSTEM_PROMPT = (
    "You rewrite analytical answers for business readers. Use ONLY the facts provided. Do not add numbers, "
    "sources, competitors or causes that are not in the facts. Keep hypotheses labelled as hypotheses. "
    "Never claim causation. If facts are insufficient, say so. Answer in at most 120 words."
)


class AIProvider(ABC):
    name = "abstract"
    enabled = False

    @abstractmethod
    def summarise(self, question: str, facts: list[str], hypotheses: list[str]) -> str | None: ...


class NoAIProvider(AIProvider):
    name = "none"
    enabled = False

    def summarise(self, question, facts, hypotheses):
        return None


class OpenAICompatibleProvider(AIProvider):  # pragma: no cover - needs a running model
    name = "openai_compatible"
    enabled = True

    def __init__(self, base_url: str, model: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

    def summarise(self, question, facts, hypotheses):
        body = {"model": self.model, "temperature": 0.1, "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"question": question, "facts": facts[:20], "hypotheses": hypotheses[:8]})}]}
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            log.warning("AI provider call failed: %s", exc)
            return None


_provider: AIProvider | None = None


def get_ai_provider() -> AIProvider:
    global _provider
    if _provider is None:
        if settings.ai_provider == "openai_compatible" and settings.ai_base_url and settings.ai_model:
            _provider = OpenAICompatibleProvider(settings.ai_base_url, settings.ai_model, settings.ai_api_key)
        else:
            _provider = NoAIProvider()
    return _provider
