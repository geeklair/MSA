"""
msa/model.py — Model client abstraction.

Provides a single complete() method regardless of which backend is configured.
The rest of the codebase never imports anthropic, openai, or requests directly.

Supported backends
------------------
anthropic   Default. Requires ANTHROPIC_API_KEY env var (or api_key in config).
            Uses the Messages API with a separate system turn.

vllm        OpenAI-compatible endpoint, typically http://localhost:8000/v1.
            Also works with a real OpenAI API key by pointing base_url at
            https://api.openai.com/v1.

ollama      Local model server at http://localhost:11434/api/generate.
            Concatenates system and user prompts into a single string because
            the Ollama generate API does not have a native system field.

Backend selection
-----------------
Set in config/config.yaml:
  model:
    backend: anthropic   # or vllm or ollama
    model: claude-sonnet-4-20250514
    max_tokens: 1024
    base_url: ...        # required for vllm/ollama if not using defaults

All backends re-raise on API errors after logging. The exception propagates to
Agent.run_once(), which catches it, preserves pre-cycle state, and logs the failure.
"""

import logging
import os

logger = logging.getLogger(__name__)


class ModelClient:
    """
    Thin wrapper around LLM API clients.

    complete(system, user) is the only public method. It accepts two plain
    strings and returns a plain string — the model's text response. The caller
    (Agent.run_cycle) is responsible for interpreting that text.
    """

    def __init__(self, config: dict):
        self.backend = config.get("backend", "anthropic")
        self.model = config.get("model", "claude-sonnet-4-20250514")
        self.max_tokens = config.get("max_tokens", 1024)
        self.base_url = config.get("base_url", None)
        self.api_key = config.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    def complete(self, system: str, user: str) -> str:
        """
        Send a system + user prompt to the configured backend; return response text.

        Args:
            system: The system turn (contents of rules.md). Constant per cycle.
            user:   The user turn (scratchpad state + tool list + instructions).
                    Rebuilt each iteration to include updated notes.

        Returns:
            The model's raw text response. Expected to be a JSON object but
            may be prose-wrapped; the dispatcher handles both cases.

        Raises:
            ValueError if backend is not recognized.
            Backend-specific exceptions (anthropic.APIError, etc.) on API failure.
        """
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
