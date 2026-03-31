"""
msa/model.py — Model client abstraction.

Supports:
  - Anthropic API (claude-sonnet-4-20250514)
  - vLLM (OpenAI-compatible endpoint)
  - Ollama (local models)
"""

import logging
import os

logger = logging.getLogger(__name__)


class ModelClient:
    def __init__(self, config: dict):
        self.backend = config.get("backend", "anthropic")
        self.model = config.get("model", "claude-sonnet-4-20250514")
        self.max_tokens = config.get("max_tokens", 1024)
        self.base_url = config.get("base_url", None)
        self.api_key = config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    def complete(self, system: str, user: str) -> str:
        if self.backend == "anthropic":
            return self._anthropic(system, user)
        elif self.backend in ("vllm", "openai"):
            return self._openai_compat(system, user)
        elif self.backend == "ollama":
            return self._ollama(system, user)
        else:
            raise ValueError(f"Unknown backend: {self.backend}")

    def _anthropic(self, system: str, user: str) -> str:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            msg = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}]
            )
            return msg.content[0].text
        except Exception as e:
            logger.error("Anthropic API error: %s", e)
            raise

    def _openai_compat(self, system: str, user: str) -> str:
        """Works with vLLM, OpenAI, or any OpenAI-compatible endpoint."""
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url=self.base_url or "http://localhost:8000/v1",
                api_key=self.api_key or "EMPTY"
            )
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ]
            )
            return resp.choices[0].message.content
        except Exception as e:
            logger.error("OpenAI-compat API error: %s", e)
            raise

    def _ollama(self, system: str, user: str) -> str:
        try:
            import requests
            url = self.base_url or "http://localhost:11434/api/generate"
            payload = {
                "model": self.model,
                "prompt": f"[SYSTEM]\n{system}\n\n[USER]\n{user}",
                "stream": False
            }
            resp = requests.post(url, json=payload, timeout=120)
            return resp.json()["response"]
        except Exception as e:
            logger.error("Ollama error: %s", e)
            raise
