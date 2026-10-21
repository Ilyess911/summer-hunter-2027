"""LLM providers. Anthropic is supported end to end; others plug in through `LLMProvider`."""

from __future__ import annotations

import abc
import json
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


class LLMError(Exception):
    """A non-fatal LLM failure: the pipeline falls back to deterministic results."""


@dataclass
class LLMResponse:
    data: dict[str, Any]
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str | None = None


class LLMProvider(abc.ABC):
    name = "base"
    model = ""

    @abc.abstractmethod
    def complete_json(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> LLMResponse:
        ...


class NullProvider(LLMProvider):
    name = "none"

    def complete_json(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> LLMResponse:
        raise LLMError("LLM disabled")


class AnthropicProvider(LLMProvider):
    """Claude via the official SDK, with structured JSON output (`output_config.format`)."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str = "low", refusal_fallback: bool = True, timeout: float = 120.0) -> None:
        import anthropic  # imported lazily so the app runs without the key or network

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback

    def complete_json(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> LLMResponse:
        a = self._anthropic
        kwargs: dict[str, Any] = {}
        if self.refusal_fallback:
            # Server-side fallback model on a policy refusal (beta); unknown to older SDKs, so sent raw.
            kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
            kwargs["extra_body"] = {"fallbacks": "default"}
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                **kwargs,
            )
        except a.BadRequestError as exc:
            raise LLMError(f"bad request: {exc.message}") from exc
        except a.AuthenticationError as exc:
            raise LLMError("authentication failed (check ANTHROPIC_API_KEY)") from exc
        except a.PermissionDeniedError as exc:
            raise LLMError("permission denied for this model") from exc
        except a.NotFoundError as exc:
            raise LLMError(f"model not found: {self.model}") from exc
        except a.RateLimitError as exc:
            raise LLMError("rate limited") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("network error") from exc

        usage = response.usage
        tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) \
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        if response.stop_reason == "refusal":
            raise LLMError("model refused the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("output truncated (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("invalid JSON from model") from exc
        return LLMResponse(data=data, input_tokens=tokens_in, output_tokens=usage.output_tokens or 0,
                           model=getattr(response, "model", self.model), stop_reason=response.stop_reason)


class OllamaProvider(LLMProvider):
    """Local model through Ollama's /api/chat with a JSON-schema `format` (structured output)."""

    name = "ollama"

    def __init__(self, model: str, url: str = "http://127.0.0.1:11434", temperature: float = 0.4, timeout: float = 600.0) -> None:
        import httpx

        self.model = model
        self.url = url.rstrip("/")
        self.temperature = temperature
        self._client = httpx.Client(timeout=timeout)

    def _start_server(self) -> bool:
        """Start `ollama serve` in the background if the local server is down (no admin rights needed)."""
        import shutil
        import subprocess
        import time

        exe = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
        try:
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return False
        for _ in range(30):
            if _ollama_up(self.url):
                return True
            time.sleep(1)
        return False

    def complete_json(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> LLMResponse:
        import httpx

        payload = {"model": self.model, "stream": False, "format": schema,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "options": {"temperature": self.temperature, "num_ctx": 12288, "num_predict": max_tokens}}
        try:
            resp = self._client.post(f"{self.url}/api/chat", json=payload)
        except httpx.ConnectError:
            if not self._start_server():
                raise LLMError("Ollama is not running and could not be started (install: brew install ollama)") from None
            resp = self._client.post(f"{self.url}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama error ({type(exc).__name__})") from None
        if resp.status_code != 200:
            raise LLMError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        try:
            data = json.loads(body["message"]["content"])
        except (KeyError, json.JSONDecodeError) as exc:
            raise LLMError("invalid JSON from local model") from exc
        return LLMResponse(data=data, input_tokens=int(body.get("prompt_eval_count", 0)),
                           output_tokens=int(body.get("eval_count", 0)), model=self.model)


def _ollama_up(url: str) -> bool:
    import httpx

    try:
        return httpx.get(f"{url}/api/version", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


class FakeProvider(LLMProvider):
    """Test double returning canned payloads (also handy for offline demos)."""

    name = "fake"

    def __init__(self, payload: dict[str, Any] | None = None, model: str = "fake-model", fail: bool = False) -> None:
        self.payload = payload or {}
        self.model = model
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> LLMResponse:
        self.calls.append((system, user))
        if self.fail:
            raise LLMError("fake failure")
        return LLMResponse(data=dict(self.payload), input_tokens=len(system + user) // 4, output_tokens=400, model=self.model)


def build_provider(settings) -> LLMProvider:  # noqa: ANN001 - Settings import would be circular
    cfg = settings.llm
    if cfg.get("provider") == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(settings.anthropic_api_key, cfg.get("model", "claude-opus-5-5"), cfg.get("effort", "low"),
                                 bool(cfg.get("refusal_fallback", True)))
    return NullProvider()
