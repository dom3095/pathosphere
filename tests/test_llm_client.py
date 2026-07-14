"""Tests for pathosphere/llm/client.py (3a).

All tests use mocks — no real LLM calls made.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pathosphere.llm.client import (
    LLMClient,
    _inject_json_system,
    _messages_to_text,
    _run_claude_subprocess,
    _strip_json_fence,
)


# ──────────────────────────────────────────────────────────────────────────────
# Unit helpers

def test_inject_json_system_prepends():
    msgs = [{"role": "user", "content": "hello"}]
    result = _inject_json_system(msgs)
    assert result[0]["role"] == "system"
    assert "JSON" in result[0]["content"]
    assert result[1] == msgs[0]


# ──────────────────────────────────────────────────────────────────────────────
# _strip_json_fence (CP-026 — models don't reliably honour "no markdown fences")

def test_strip_json_fence_removes_json_tagged_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _strip_json_fence(raw) == '{"a": 1}'


def test_strip_json_fence_removes_bare_fence():
    raw = '```\n{"a": 1}\n```'
    assert _strip_json_fence(raw) == '{"a": 1}'


def test_strip_json_fence_noop_on_clean_json():
    raw = '{"a": 1}'
    assert _strip_json_fence(raw) == '{"a": 1}'


def test_strip_json_fence_handles_surrounding_whitespace():
    raw = '  \n```json\n{"a": 1}\n```\n  '
    assert _strip_json_fence(raw) == '{"a": 1}'


def test_strip_json_fence_multiline_body():
    raw = '```json\n{\n  "theses": [1, 2, 3]\n}\n```'
    assert json.loads(_strip_json_fence(raw)) == {"theses": [1, 2, 3]}


def test_strip_json_fence_trailing_prose_after_closing_fence():
    """CP-026 regression: an anchored end-of-string match missed this case —
    the model adds a sentence after the closing fence despite instructions
    not to add text outside the JSON structure."""
    raw = '```json\n{"a": 1}\n```\nHope this helps!'
    assert _strip_json_fence(raw) == '{"a": 1}'


def test_strip_json_fence_leading_and_trailing_prose():
    raw = 'Sure, here is the JSON:\n```json\n{"a": 1}\n```\nLet me know if you need more.'
    assert _strip_json_fence(raw) == '{"a": 1}'


def test_complete_claude_json_mode_strips_fence(monkeypatch):
    """Integration: LLMClient.complete(json_mode=True) must hand callers
    clean JSON even when the model wraps it in a fence despite instructions
    not to (the actual failure mode hit on the first real thesis-generate
    run, 2026-07-14)."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '```json\n{"theses": []}\n```'
    mock_result.stderr = ""

    client = LLMClient(backend="claude")
    with patch("subprocess.run", return_value=mock_result):
        result = asyncio.run(
            client.complete([{"role": "user", "content": "Give me JSON."}], json_mode=True)
        )

    assert result == '{"theses": []}'
    assert json.loads(result) == {"theses": []}


def test_complete_claude_non_json_mode_does_not_strip():
    """Fence-stripping only applies when json_mode=True — a prose response
    that happens to contain a code block must pass through untouched."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Here is code:\n```python\nprint(1)\n```"
    mock_result.stderr = ""

    client = LLMClient(backend="claude")
    with patch("subprocess.run", return_value=mock_result):
        result = asyncio.run(
            client.complete([{"role": "user", "content": "Show me code."}])
        )

    assert result == "Here is code:\n```python\nprint(1)\n```"


def test_inject_json_system_merges_existing():
    msgs = [
        {"role": "system", "content": "Be helpful."},
        {"role": "user", "content": "hello"},
    ]
    result = _inject_json_system(msgs)
    assert result[0]["role"] == "system"
    assert "Be helpful." in result[0]["content"]
    assert "JSON" in result[0]["content"]
    assert len(result) == 2


def test_messages_to_text_user_only():
    msgs = [{"role": "user", "content": "What is 2+2?"}]
    text = _messages_to_text(msgs)
    assert "What is 2+2?" in text


def test_messages_to_text_system_prepended():
    msgs = [
        {"role": "system", "content": "You are a geopolitics expert."},
        {"role": "user", "content": "Summarize Taiwan tensions."},
    ]
    text = _messages_to_text(msgs)
    # System block should appear before user content
    assert text.index("[SYSTEM]") < text.index("Summarize Taiwan tensions.")


def test_messages_to_text_multi_turn():
    msgs = [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Follow-up"},
    ]
    text = _messages_to_text(msgs)
    assert "First question" in text
    assert "First answer" in text
    assert "Follow-up" in text


# ──────────────────────────────────────────────────────────────────────────────
# LLMClient construction

def test_llm_client_default_backend(monkeypatch):
    monkeypatch.setenv("REASONING_MODEL", "qwen-local")
    # Reset cached settings so the env var takes effect
    import pathosphere.config as cfg
    cfg._settings = None
    client = LLMClient()
    assert client._backend == "qwen-local"
    cfg._settings = None  # cleanup


def test_llm_client_explicit_backend():
    client = LLMClient(backend="claude")
    assert client._backend == "claude"


def test_llm_client_invalid_backend():
    with pytest.raises(ValueError, match="Unknown reasoning_model"):
        LLMClient(backend="gpt4")  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# Claude backend

def test_run_claude_subprocess_success():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "  Hello from Claude  "
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        out = _run_claude_subprocess("test prompt")

    assert out == "Hello from Claude"
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == ["claude", "-p", "--safe-mode", "--tools=", "test prompt"]


def test_run_claude_subprocess_uses_safe_mode_no_tools():
    """CP-026 regression guard: the subprocess must run isolated from this
    repo's CLAUDE.md/hooks (--safe-mode) and with no tool access (--tools=),
    so pipeline completions can't be contaminated by coding-session framing
    or perform file/bash side effects. Must NOT use --bare — that requires
    ANTHROPIC_API_KEY and skips OAuth/keychain auth, which this project's
    subscription-credit setup relies on."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "ok"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        _run_claude_subprocess("test prompt")

    cmd = mock_run.call_args[0][0]
    assert "--safe-mode" in cmd
    assert "--tools=" in cmd
    assert "--bare" not in cmd
    assert cmd[-1] == "test prompt"


def test_run_claude_subprocess_failure():
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "claude not found"

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="exited 1"):
            _run_claude_subprocess("test prompt")


def test_complete_claude_calls_subprocess():
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Analysis complete."
    mock_result.stderr = ""

    client = LLMClient(backend="claude")

    with patch("subprocess.run", return_value=mock_result):
        result = asyncio.run(
            client.complete([{"role": "user", "content": "Analyze this."}])
        )

    assert result == "Analysis complete."


def test_complete_claude_json_mode_injects_system():
    """With json_mode=True the subprocess receives a prompt containing JSON instructions."""
    captured_prompts: list[str] = []

    def fake_run(cmd, **kwargs):
        captured_prompts.append(cmd[-1])  # prompt is always the last arg
        r = MagicMock()
        r.returncode = 0
        r.stdout = '{"key": "value"}'
        r.stderr = ""
        return r

    client = LLMClient(backend="claude")

    with patch("subprocess.run", side_effect=fake_run):
        asyncio.run(
            client.complete(
                [{"role": "user", "content": "Give me JSON."}],
                json_mode=True,
            )
        )

    assert captured_prompts, "subprocess.run was not called"
    assert "JSON" in captured_prompts[0]


# ──────────────────────────────────────────────────────────────────────────────
# Qwen-local backend

def test_complete_qwen_success():
    fake_response_data = {
        "choices": [{"message": {"content": "Qwen says hello."}}]
    }

    client = LLMClient(backend="qwen-local")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_response_data
    mock_resp.raise_for_status = MagicMock()

    async def run():
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_http

            return await client.complete(
                [{"role": "user", "content": "Hello"}]
            )

    result = asyncio.run(run())
    assert result == "Qwen says hello."


def test_complete_qwen_uses_900s_timeout():
    """CP-029: measured a real single research-prompt call at 318.7s (not the
    46-113s seen for small classification prompts in CP-022) — 900s leaves
    real margin for the larger divergence/critique prompts in the debate
    pipeline, which is documented as a background-only command, not a
    latency-sensitive path."""
    client = LLMClient(backend="qwen-local")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    mock_resp.raise_for_status = MagicMock()

    async def run():
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_http

            await client.complete([{"role": "user", "content": "hi"}])
            return mock_cls

    mock_cls = asyncio.run(run())
    assert mock_cls.call_args.kwargs["timeout"] == 900


def test_complete_qwen_custom_model():
    """model= override is passed through to the HTTP payload."""
    payloads: list[dict] = []

    client = LLMClient(backend="qwen-local")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "ok"}}]
    }
    mock_resp.raise_for_status = MagicMock()

    async def run():
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)

            async def fake_post(url, json=None, **kwargs):
                payloads.append(json or {})
                return mock_resp

            mock_http.post = fake_post
            mock_cls.return_value = mock_http

            return await client.complete(
                [{"role": "user", "content": "hi"}],
                model="qwen3:8b",
            )

    asyncio.run(run())
    assert payloads[0]["model"] == "qwen3:8b"


def test_complete_qwen_connect_error():
    """ConnectError is re-raised as a descriptive RuntimeError."""
    import httpx as _httpx

    client = LLMClient(backend="qwen-local")

    async def run():
        with patch("httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(
                side_effect=_httpx.ConnectError("refused")
            )
            mock_cls.return_value = mock_http

            return await client.complete([{"role": "user", "content": "hi"}])

    with pytest.raises(RuntimeError, match="Ollama"):
        asyncio.run(run())


# ──────────────────────────────────────────────────────────────────────────────
# __init__ export

def test_llm_init_exports_client():
    from pathosphere.llm import LLMClient as _Cls
    assert _Cls is LLMClient
