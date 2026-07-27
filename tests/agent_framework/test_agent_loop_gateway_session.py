"""
@file_name: test_agent_loop_gateway_session.py
@author: Bin Liang
@date: 2026-07-23
@description: Backend-side free-tier gateway session lifecycle —
`open_backend_session` mints the per-run key and injects it into the
ClaudeConfig ContextVar (so it rides provider_configs to the executor), aborts
cleanly on failure without falling back to the master key, and step_3 revokes
it in a finally (source-inspection guard).

The mint MUST live on the backend orchestrator, not inside the executor: the
executor runs user-controlled agent code and must never hold the gateway admin
key. These tests lock that contract in.
"""
import inspect

import pytest

from xyz_agent_context.agent_framework import api_config
from xyz_agent_context.agent_framework.providers import gateway_key_service as gks_mod
from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    OpenAIConfig,
    set_user_config,
)
from xyz_agent_context.agent_framework.providers.gateway_key_service import (
    BackendGatewaySession,
    MintedSessionKey,
    open_backend_session,
)


class _FakeSvc:
    def __init__(self, minted):
        self._minted = minted
        self.revoked = []

    async def mint_session_key(self, user_id, agent_id=None, run_id=None):
        return self._minted

    async def revoke_session_key(self, run_id):
        self.revoked.append(run_id)


@pytest.fixture
def _wire(monkeypatch):
    """Set the ContextVars open_backend_session reads, and patch from_env."""
    def _install(provider_source, svc, *, claude=None):
        # Seed the resolved configs the way the resolver would (system tier:
        # empty placeholder api_key, base_url pointing at the gateway).
        set_user_config(
            claude=claude or ClaudeConfig(api_key="", base_url="http://litellm:4000"),
            openai=OpenAIConfig(api_key="sk-backend-helper", base_url="http://litellm:4000/v1"),
        )
        monkeypatch.setattr(api_config, "get_provider_source", lambda: provider_source)
        monkeypatch.setattr(api_config, "get_current_user_id", lambda: "usr_x")
        monkeypatch.setattr(
            gks_mod.GatewayKeyService, "from_env", classmethod(lambda cls, db: svc)
        )

    return _install


@pytest.mark.asyncio
async def test_non_system_run_is_noop(_wire):
    _wire(provider_source="user", svc=None)
    session, ok = await open_backend_session(db=object())
    assert session is None and ok is True
    # ContextVar untouched.
    assert api_config.snapshot_user_config()["claude"].api_key == ""


@pytest.mark.asyncio
async def test_system_run_injects_ticket_into_contextvar(_wire):
    minted = MintedSessionKey(
        run_id="sess_x", key="sk-ticket", base_url="http://litellm:4000"
    )
    fake = _FakeSvc(minted)
    _wire(provider_source="system", svc=fake)

    session, ok = await open_backend_session(db=object(), agent_id="agt_1")

    assert ok is True
    assert isinstance(session, BackendGatewaySession)
    assert session.run_id == "sess_x"
    # The ticket is now in the ClaudeConfig ContextVar → will serialize into
    # provider_configs and reach the executor.
    claude = api_config.snapshot_user_config()["claude"]
    assert claude.api_key == "sk-ticket"
    assert claude.base_url == "http://litellm:4000"
    # Helper slot is left intact (its backend gateway key).
    assert api_config.snapshot_user_config()["openai"].api_key == "sk-backend-helper"

    # close() revokes.
    await session.close()
    assert fake.revoked == ["sess_x"]


@pytest.mark.asyncio
async def test_system_run_aborts_when_gateway_unconfigured(_wire):
    # from_env returns None → misconfig / not deployed. Must abort (ok=False).
    _wire(provider_source="system", svc=None)
    session, ok = await open_backend_session(db=object())
    assert session is None and ok is False
    # No durable/placeholder key promoted; ContextVar still the empty placeholder.
    assert api_config.snapshot_user_config()["claude"].api_key == ""


@pytest.mark.asyncio
async def test_system_run_aborts_when_mint_fails(_wire):
    _wire(provider_source="system", svc=_FakeSvc(None))
    session, ok = await open_backend_session(db=object())
    assert session is None and ok is False


@pytest.mark.asyncio
async def test_step3_gateway_abort_yields_terminal_result(monkeypatch, tmp_path):
    """Regression: the gateway-unavailable branch must still yield a terminal
    PathExecutionResult. step_3_execute_path asserts ctx.execution_result is set
    and AgentRuntime.run() has no guard — an early bare return would crash the
    runtime on the free tier's main failure branch."""
    from types import SimpleNamespace

    import importlib

    # The package re-exports the function `step_3_agent_loop`, shadowing the
    # submodule of the same name — importlib gets the real module to patch.
    s3 = importlib.import_module(
        "xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop"
    )
    from xyz_agent_context.agent_framework.providers import gateway_key_service as gks
    from xyz_agent_context.schema import ErrorMessage, PathExecutionResult
    from xyz_agent_context.settings import settings

    fake_context = SimpleNamespace(
        messages=[], mcp_servers={}, disallowed_tools=[],
        ctx_data=SimpleNamespace(extra_data=None),
    )

    class _FakeContextRuntime:
        def __init__(self, *a, **k):
            pass

        async def run(self, *a, **k):
            return fake_context

    async def _fake_framework(agent_id, db):
        return "claude_code"

    async def _gateway_down(db, agent_id=None):
        return None, False  # ok=False → abort branch

    monkeypatch.setattr(s3, "ContextRuntime", _FakeContextRuntime)
    monkeypatch.setattr(s3, "_resolve_agent_framework_name", _fake_framework)
    monkeypatch.setattr(gks, "open_backend_session", _gateway_down)
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))

    ctx = SimpleNamespace(
        agent_id="agt_1", user_id="usr_1", narrative_list=[], active_instances=[],
        input_content="hi", working_source="chat", created_job_ids=[],
        trigger_extra_data={}, mcp_servers={},
    )

    # Consume EXACTLY as step_3_execute_path does.
    execution_result = None
    saw_gateway_error = False
    async for msg in s3.step_3_agent_loop(ctx, db_client=None, response_processor=None):
        if isinstance(msg, PathExecutionResult):
            execution_result = msg
        elif isinstance(msg, ErrorMessage) and msg.error_type == "gateway_unavailable":
            saw_gateway_error = True

    # The invariant step_3_execute_path's assert depends on:
    assert execution_result is not None, "abort path must yield a terminal PathExecutionResult"
    assert saw_gateway_error, "abort path must surface the gateway_unavailable error"


def test_step3_mints_and_revokes_in_finally():
    """Lock the correct LAYER + lifecycle: the mint is in the backend step_3
    (not the executor), and revoke runs in a finally so the ticket is released
    on success, error, AND cancel (铁律 #14 — run-lifecycle bound, no timer)."""
    from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
        step_3_agent_loop,
    )

    src = inspect.getsource(step_3_agent_loop)
    assert "open_backend_session" in src
    finally_idx = src.rfind("finally:")
    close_idx = src.rfind("gw_session.close()")
    assert finally_idx != -1 and close_idx > finally_idx, (
        "gw_session.close() must run inside step_3's finally so the ticket is "
        "revoked on every exit path."
    )
