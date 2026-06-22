"""
LLM abstraction with two backends:
- claude:      calls `claude -p "PROMPT"` via subprocess (Agent SDK)
- qwen-local:  OpenAI-compatible HTTP to Ollama at http://localhost:11434/v1
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Literal

import httpx
from loguru import logger

from pathosphere.config import get_settings

Backend = Literal["claude", "qwen-local"]

_OLLAMA_BASE = "http://localhost:11434/v1"
_QWEN_MODEL = "qwen3:4b"

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

    # ──────────────────────────────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        json_mode: bool = False,
    ) -> str:
        """Call the backend and return the assistant message as a string.

        Args:
            messages:  OpenAI-style chat messages (role/content dicts).
            model:     Optional model override (only used by qwen-local backend).
            json_mode: Prepend a system prompt that enforces JSON-only output.
        """
        if json_mode:
            messages = _inject_json_system(messages)

        if self._backend == "claude":
            return await self._complete_claude(messages)
        else:
            return await self._complete_qwen(messages, model=model)

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
        self, messages: list[dict], *, model: str | None
    ) -> str:
        mdl = model or _QWEN_MODEL
        url = f"{_OLLAMA_BASE}/chat/completions"
        payload: dict = {"model": mdl, "messages": messages, "stream": False}

        logger.debug(f"LLM/qwen → {url} model={mdl}")

        async with httpx.AsyncClient(timeout=120) as client:
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
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


# ──────────────────────────────────────────────────────────────────────────────
# Helpers


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

    Raises RuntimeError if the process exits non-zero.
    """
    result = subprocess.run(
        ["claude", "-p", prompt],
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
