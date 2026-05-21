"""
@file_name: test_lark_cli_download.py
@date: 2026-05-21
@description: Tests for ``LarkCLIClient.fetch_message_resource`` and the
``capture_binary`` extension of ``_exec_lark_cli``. Phase 1c T8 + T19.

The CLI subprocess itself is mocked at the ``_run_with_agent_id`` /
``_exec_lark_cli`` boundary — the side-effect (writing bytes to the
``--output`` path) is implemented by the fakes so that downstream
read-and-return logic in ``fetch_message_resource`` is exercised
realistically.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xyz_agent_context.module.lark_module.lark_cli_client import LarkCLIClient


def _extract_output_path(args: list[str]) -> str:
    """Find the value following ``--output`` in a lark-cli arg list."""
    for i, a in enumerate(args):
        if a == "--output" and i + 1 < len(args):
            return args[i + 1]
    return ""


def _extract_params_type(args: list[str]) -> str:
    """Find the ``type`` field inside ``--params <json>``."""
    for i, a in enumerate(args):
        if a == "--params" and i + 1 < len(args):
            try:
                return json.loads(args[i + 1]).get("type", "")
            except (json.JSONDecodeError, TypeError):
                return ""
    return ""


@pytest.fixture
def client() -> LarkCLIClient:
    """Cheap client — fetch_message_resource bypasses DB via _run_with_agent_id mock."""
    return LarkCLIClient()


# ─────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_message_resource_returns_downloaded_bytes(client, monkeypatch) -> None:
    expected_bytes = b"%PDF-1.4\n%fake pdf body"
    captured: dict = {}

    async def fake_run(args, agent_id, stdin_data="", timeout=60.0, *, capture_binary=False, **kwargs):
        captured["args"] = list(args)
        captured["agent_id"] = agent_id
        captured["capture_binary"] = capture_binary
        Path(_extract_output_path(args)).write_bytes(expected_bytes)
        return {"success": True}

    monkeypatch.setattr(client, "_run_with_agent_id", fake_run)

    got = await client.fetch_message_resource(
        agent_id="agent_xyz",
        message_id="om_msg_abc",
        file_key="file_v3_zzz",
        resource_type="file",
    )

    assert got == expected_bytes
    assert captured["agent_id"] == "agent_xyz"
    assert captured["capture_binary"] is True


@pytest.mark.asyncio
async def test_fetch_message_resource_constructs_correct_url(client, monkeypatch) -> None:
    captured: dict = {}

    async def fake_run(args, agent_id, **kwargs):
        captured["args"] = list(args)
        Path(_extract_output_path(args)).write_bytes(b"data")
        return {"success": True}

    monkeypatch.setattr(client, "_run_with_agent_id", fake_run)

    await client.fetch_message_resource(
        agent_id="a1",
        message_id="om_xxxxxxx",
        file_key="file_v3_yyyyyy",
        resource_type="file",
    )

    args = captured["args"]
    assert args[0] == "api"
    assert args[1] == "GET"
    assert args[2] == "/open-apis/im/v1/messages/om_xxxxxxx/resources/file_v3_yyyyyy"
    assert "--params" in args
    assert "--output" in args


@pytest.mark.asyncio
async def test_fetch_message_resource_passes_resource_type_in_params(client, monkeypatch) -> None:
    captured: list[str] = []

    async def fake_run(args, agent_id, **kwargs):
        Path(_extract_output_path(args)).write_bytes(b"x")
        captured.append(_extract_params_type(args))
        return {"success": True}

    monkeypatch.setattr(client, "_run_with_agent_id", fake_run)

    for rtype in ("file", "image", "audio", "video", "media"):
        await client.fetch_message_resource(
            agent_id="a1",
            message_id="m",
            file_key="k",
            resource_type=rtype,
        )

    assert captured == ["file", "image", "audio", "video", "media"]


# ─────────────────────────────────────────────────────────────────────
# Error paths
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_message_resource_raises_on_cli_error(client, monkeypatch) -> None:
    """When lark-cli returns success=False the method surfaces the error
    text via RuntimeError. Callers in ``LarkTrigger.fetch_attachments``
    catch this, audit, and skip the ref — preserving never-raise at the
    trigger boundary."""

    async def fake_run(args, agent_id, **kwargs):
        return {
            "success": False,
            "error": "permission denied: missing scope im:resource",
        }

    monkeypatch.setattr(client, "_run_with_agent_id", fake_run)

    with pytest.raises(RuntimeError, match="permission denied"):
        await client.fetch_message_resource(
            agent_id="a1",
            message_id="om_x",
            file_key="file_x",
            resource_type="file",
        )


@pytest.mark.asyncio
async def test_fetch_message_resource_raises_when_output_missing(client, monkeypatch) -> None:
    """lark-cli reported success but didn't actually write bytes
    (defensive — should never happen, but better than silent empty bytes)."""

    async def fake_run(args, agent_id, **kwargs):
        # Don't write anything; simulate broken lark-cli build.
        return {"success": True}

    monkeypatch.setattr(client, "_run_with_agent_id", fake_run)

    with pytest.raises(RuntimeError, match="empty"):
        await client.fetch_message_resource(
            agent_id="a1",
            message_id="om_x",
            file_key="file_x",
            resource_type="file",
        )


# ─────────────────────────────────────────────────────────────────────
# Tmpfile lifecycle
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tmpfile_removed_after_successful_read(client, monkeypatch) -> None:
    captured_paths: list[str] = []

    async def fake_run(args, agent_id, **kwargs):
        out_path = _extract_output_path(args)
        captured_paths.append(out_path)
        Path(out_path).write_bytes(b"data")
        return {"success": True}

    monkeypatch.setattr(client, "_run_with_agent_id", fake_run)

    await client.fetch_message_resource(
        agent_id="a1", message_id="x", file_key="y", resource_type="image",
    )

    assert len(captured_paths) == 1
    assert not Path(captured_paths[0]).exists(), (
        "tmpfile MUST be removed after successful read"
    )


@pytest.mark.asyncio
async def test_tmpfile_removed_even_on_cli_error(client, monkeypatch) -> None:
    captured_paths: list[str] = []

    async def fake_run(args, agent_id, **kwargs):
        captured_paths.append(_extract_output_path(args))
        # CLI errored; don't write the tmpfile.
        return {"success": False, "error": "boom"}

    monkeypatch.setattr(client, "_run_with_agent_id", fake_run)

    with pytest.raises(RuntimeError):
        await client.fetch_message_resource(
            agent_id="a1", message_id="x", file_key="y", resource_type="file",
        )

    assert len(captured_paths) == 1
    # The tmpfile may or may not exist (lark-cli may have created an empty
    # one before erroring). The contract is that we don't LEAK it — i.e.
    # the path must be gone after the call returns.
    assert not Path(captured_paths[0]).exists()


# ─────────────────────────────────────────────────────────────────────
# capture_binary extension of _exec_lark_cli — direct unit check
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_exec_lark_cli_capture_binary_skips_stdout_json_parse(client, monkeypatch) -> None:
    """When capture_binary=True, _exec_lark_cli must NOT try to parse
    stdout as JSON. The contract: success returns ``{"success": True}``
    with no ``data`` field — the caller reads bytes from --output."""

    class _FakeProc:
        returncode = 0

        async def communicate(self, input=None):
            return (b"", b"")  # empty stdout (typical for --output mode)

    async def fake_create_subprocess_exec(*a, **kw):
        return _FakeProc()

    import asyncio as _asyncio
    monkeypatch.setattr(_asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await client._exec_lark_cli(
        cmd=["lark-cli", "api", "GET", "/foo", "--output", "/tmp/x"],
        stdin_data="",
        timeout=5.0,
        capture_binary=True,
    )
    assert result == {"success": True}


@pytest.mark.asyncio
async def test_exec_lark_cli_capture_binary_still_surfaces_error_envelope(
    client, monkeypatch
) -> None:
    """capture_binary path must still parse error JSON when lark-cli
    exits non-zero (matches the text-mode error handling)."""

    class _FakeProc:
        returncode = 1

        async def communicate(self, input=None):
            stdout = json.dumps({
                "error": {"message": "scope missing: im:resource"}
            }).encode()
            return (stdout, b"")

    async def fake_create_subprocess_exec(*a, **kw):
        return _FakeProc()

    import asyncio as _asyncio
    monkeypatch.setattr(_asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await client._exec_lark_cli(
        cmd=["lark-cli", "api", "GET", "/foo", "--output", "/tmp/x"],
        stdin_data="",
        timeout=5.0,
        capture_binary=True,
    )
    assert result["success"] is False
    assert "scope missing" in result["error"]
