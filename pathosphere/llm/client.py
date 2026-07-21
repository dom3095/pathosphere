"""
LLM abstraction with two backends:
- claude:      calls `claude -p "PROMPT"` via subprocess (Agent SDK)
- qwen-local:  OpenAI-compatible HTTP to Ollama at http://localhost:11434/v1
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from typing import Literal

import httpx
from loguru import logger

from pathosphere.config import get_settings

Backend = Literal["claude", "qwen-local"]

_OLLAMA_BASE = "http://localhost:11434/v1"
_QWEN_MODEL = "qwen3:4b"

# CP-029: per-call latency on qwen3:4b (M1 8GB, CPU-bound) is highly variable
# and grows over a long session (~370s early → >900s after ~50 min in the same
# run, cause not yet isolated — thermal throttling vs. concurrent processes).
# 1800s absorbs the worst observed spikes; one retry distinguishes a transient
# spike from a hard limit without adding real complexity.
_QWEN_TIMEOUT_S = 1800
_QWEN_READ_TIMEOUT_RETRIES = 1

_JSON_SYSTEM = (
    "Respond ONLY with valid JSON. Do not include markdown fences, "
    "explanations, or any text outside the JSON structure."
)


class LLMClient:
    """Thin async wrapper around the configured LLM backend.

    Usage:
        client = LLMClient()
        text = await client.complete([{"role": "user", "content": "Hello"}])
    """

    def __init__(self, backend: Backend | None = None) -> None:
        settings = get_settings()
        self._backend: Backend = backend or settings.reasoning_model  # type: ignore[assignment]
        if self._backend not in ("claude", "qwen-local"):
            raise ValueError(
                f"Unknown reasoning_model '{self._backend}'. "
                "Use 'claude' or 'qwen-local'."
            )
        # Capability cache (qwen-local only): set once a response_format
        # rejection is confirmed, so later calls on this same instance skip
        # straight to the unconstrained request instead of paying a doomed
        # extra round-trip on every call for the rest of the run.
        self._schema_unsupported = False

    # ──────────────────────────────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        json_mode: bool = False,
        json_schema: dict | None = None,
    ) -> str:
        """Call the backend and return the assistant message as a string.

        Args:
            messages:    OpenAI-style chat messages (role/content dicts).
            model:       Optional model override (only used by qwen-local backend).
            json_mode:   Prepend a system prompt that enforces JSON-only output.
            json_schema: JSON Schema the response must conform to. Enforced
                server-side for qwen-local (Ollama `response_format`,
                grammar-constrained decoding — not just a prose instruction);
                a no-op for claude beyond the json_mode prose ask (passing it
                there is harmless, just doesn't add server-side enforcement).
                Implies json_mode (fence-stripping still applies).
        """
        want_json = json_mode or json_schema is not None
        if want_json:
            messages = _inject_json_system(messages)

        if self._backend == "claude":
            raw = await self._complete_claude(messages)
        else:
            raw = await self._complete_qwen(messages, model=model, json_schema=json_schema)

        return _strip_json_fence(raw) if want_json else raw

    # ──────────────────────────────────────────────────────────────────────────
    # Claude via Agent SDK subprocess

    async def _complete_claude(self, messages: list[dict]) -> str:
        prompt = _messages_to_text(messages)
        logger.debug(f"LLM/claude prompt ({len(prompt)} chars)")

        try:
            result = await asyncio.to_thread(
                _run_claude_subprocess, prompt
            )
        except Exception as exc:
            logger.error(f"LLM/claude subprocess failed: {exc}")
            raise

        logger.debug(f"LLM/claude response ({len(result)} chars)")
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Qwen-local via Ollama OpenAI-compatible API

    async def _complete_qwen(
        self, messages: list[dict], *, model: str | None, json_schema: dict | None = None
    ) -> str:
        mdl = model or _QWEN_MODEL
        # Once a rejection is confirmed for this instance, stop asking —
        # every later call would otherwise pay the same doomed extra
        # round-trip (see CP-032).
        schema = json_schema if not self._schema_unsupported else None
        payload: dict = {"model": mdl, "messages": messages, "stream": False}
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema},
            }

        try:
            return await self._post_qwen(payload)
        except httpx.HTTPStatusError as exc:
            if schema is None or exc.response.status_code != 400 or not _mentions_schema(exc.response):
                raise
            # Confirmed (body mentions response_format/schema, not just any
            # 400 — an unrelated client error, e.g. bad model name, must
            # still surface normally): this Ollama/model build rejects the
            # schema constraint. Degrade to unconstrained JSON mode instead
            # of failing the whole call, and remember it for the rest of
            # this client's lifetime. Live-confirmed working on Ollama
            # 0.31.1 (2026-07-21, 200/200 calls, zero rejections) — this
            # branch exists for a downgrade/model-swap, not the common case.
            logger.warning(
                "LLM/qwen response_format rejected (400, schema-related) — "
                "retrying without it, caching as unsupported for this client"
            )
            self._schema_unsupported = True
            payload.pop("response_format")
            return await self._post_qwen(payload)

    async def _post_qwen(self, payload: dict) -> str:
        url = f"{_OLLAMA_BASE}/chat/completions"
        logger.debug(f"LLM/qwen → {url} model={payload['model']}")

        for attempt in range(_QWEN_READ_TIMEOUT_RETRIES + 1):
            async with httpx.AsyncClient(timeout=_QWEN_TIMEOUT_S) as client:
                try:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                except httpx.ReadTimeout:
                    if attempt >= _QWEN_READ_TIMEOUT_RETRIES:
                        raise
                    logger.warning(
                        f"LLM/qwen ReadTimeout after {_QWEN_TIMEOUT_S}s "
                        f"(attempt {attempt + 1}), retrying once"
                    )
                    continue
                except httpx.HTTPStatusError as exc:
                    logger.error(
                        f"LLM/qwen HTTP {exc.response.status_code}: {exc.response.text[:200]}"
                    )
                    raise
                except httpx.ConnectError:
                    raise RuntimeError(
                        f"Cannot reach Ollama at {_OLLAMA_BASE}. "
                        "Is the server running? (`ollama serve`)"
                    )

            data = resp.json()
            content: str = data["choices"][0]["message"]["content"]
            logger.debug(f"LLM/qwen response ({len(content)} chars)")
            return content

        raise AssertionError("unreachable")  # pragma: no cover


# ──────────────────────────────────────────────────────────────────────────────
# Helpers


def _mentions_schema(response: httpx.Response) -> bool:
    """Does this 400's body actually mention response_format/schema, or is
    it an unrelated client error (bad model name, malformed messages) that
    merely also happens to be a 400? Conservative on purpose — only treat a
    400 as schema rejection when the body says so; otherwise the caller
    re-raises so the real error surfaces instead of being silently retried
    away as if it were a compatibility quirk."""
    text = response.text.lower()
    return "response_format" in text or "json_schema" in text or "schema" in text


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _strip_json_fence(raw: str) -> str:
    """Extract a ```json ... ``` (or bare ``` ... ```) fenced block from the
    response, ignoring any prose before or after it.

    The json_mode system prompt explicitly says "no markdown fences" and "no
    text outside the JSON structure", but models don't reliably honour
    either (observed on the real thesis-generation pipeline, CP-026 —
    including trailing prose AFTER the closing fence, e.g.
    '```json\\n{...}\\n```\\nHope this helps!', which an
    anchored-to-end-of-string match would miss). Searches for the first
    fenced block anywhere in the string instead of anchoring the whole
    string, so leading and trailing text around the fence are both
    discarded. Centralized here so every current and future json_mode
    caller gets clean JSON without repeating this. No-op if there's no
    fence at all (returns the input stripped, unchanged shape).
    """
    stripped = raw.strip()
    m = _JSON_FENCE_RE.search(stripped)
    return m.group(1).strip() if m else stripped


def _inject_json_system(messages: list[dict]) -> list[dict]:
    """Prepend or merge a JSON-enforcement system message."""
    if messages and messages[0].get("role") == "system":
        existing = messages[0]["content"]
        merged = f"{existing}\n\n{_JSON_SYSTEM}"
        return [{"role": "system", "content": merged}] + messages[1:]
    return [{"role": "system", "content": _JSON_SYSTEM}] + messages


def _messages_to_text(messages: list[dict]) -> str:
    """Flatten chat messages into a single prompt string for `claude -p`.

    System messages are prepended as context; assistant turns are included
    verbatim so multi-turn conversations work correctly.
    """
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.insert(0, f"[SYSTEM]\n{content}\n[/SYSTEM]")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
        else:
            parts.append(content)
    return "\n\n".join(parts)


def _run_claude_subprocess(prompt: str) -> str:
    """Run `claude -p PROMPT` synchronously and return stdout.

    `--safe-mode` disables CLAUDE.md/hooks/skills/plugins for this call —
    without it, the subprocess inherits this repo's CLAUDE.md (caveman-mode
    tone instructions, coding-session framing) and produces contaminated
    output for what should be a plain text completion (observed on the
    2026-07-14 first real run: brief content prefixed/suffixed with
    conversational meta-commentary — "saved to scratchpad", "tell me if you
    want this wired into brief.py" — see CP-026 in CRITICAL_POINTS.md).
    `--tools=` disables tool access so this stays a pure text completion,
    never an agentic session with file/bash side effects. Deliberately NOT
    `--bare`: that also skips OAuth/keychain auth (API-key only), which
    would break this project's subscription-credit auth (no
    ANTHROPIC_API_KEY set here by design — see CLAUDE.md LLM strategy).

    Raises RuntimeError if the process exits non-zero.
    """
    result = subprocess.run(
        ["claude", "-p", "--safe-mode", "--tools=", prompt],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()[:500]
        raise RuntimeError(
            f"`claude -p` exited {result.returncode}: {stderr}"
        )
    return result.stdout.strip()
