"""
@file_name: test_helper_egress_pinning.py
@author: Bin Liang
@date: 2026-10-29
@description: Helper-LLM SDK clients pin egress for USER-supplied base_urls.

The helper LLM (narrative / memory / instance decisions, skill requirement
extraction) runs in the backend and workers. Its openai / anthropic SDK clients
used the SDK default transport, which follows redirects and re-resolves the
host — so a user's stored base_url, only DNS-free screened at read time, could
answer 302 -> 169.254.169.254 or rebind to an internal address. Contract:

  - the runtime resolver marks a helper config built from a user-sourced card
    (`user_endpoint=True`); platform cards (our gateway, system pool) stay
    unmarked, because their base_urls are internal by design;
  - in cloud a marked config gets an SDK http_client with the pinned transport
    and follow_redirects=False; unmarked or local -> SDK default (unchanged).
"""
from __future__ import annotations

from collections import defaultdict

import httpcore
import openai
import pytest

from narranexus.platform.agent_framework.api_config import AnthropicHelperConfig, OpenAIConfig
from narranexus.platform.agent_framework.providers.driver import resolve_user_runtime_llm_configs
from narranexus.platform.agent_framework.providers.user_service import UserProviderService
from narranexus.platform.utils import url_safety
from narranexus.platform.utils.safe_http import PublicOnlyTransport


class _FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = defaultdict(list)

    async def get(self, table, filters=None):
        filters = filters or {}
        return [r for r in self.tables[table] if all(r.get(k) == v for k, v in filters.items())]

    async def get_one(self, table, filters):
        rows = await self.get(table, filters)
        return rows[0] if rows else None

    async def insert(self, table, data):
        self.tables[table].append(dict(data))

    async def update(self, table, filters, data):
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)

    async def delete(self, table, filters):
        before = len(self.tables[table])
        self.tables[table] = [
            r for r in self.tables[table] if not all(r.get(k) == v for k, v in filters.items())
        ]
        return before - len(self.tables[table])


async def _seed(db, card_type, model, *, source=None):
    """u1: agent slot on its own anthropic card, helper slot on `card_type`."""
    svc = UserProviderService(db)
    _, agent_ids = await svc.add_provider(
        user_id="u1", card_type="anthropic", api_key="sk-agent", models=["claude-opus-4-8"],
    )
    await svc.set_slot("u1", "agent", agent_ids[0], "claude-opus-4-8", actor_is_staff=None)
    _, ids = await svc.add_provider(
        user_id="u1", card_type=card_type, api_key="sk-x",
        base_url="https://llm.example.com/v1", models=[model],
    )
    if source is not None:
        for row in db.tables["user_providers"]:
            if row["provider_id"] == ids[0]:
                row["source"] = source
    await svc.set_slot("u1", "helper_llm", ids[0], model, actor_is_staff=None)


# ── resolver marks user-sourced helper configs ──

@pytest.mark.asyncio
async def test_user_openai_helper_card_is_marked_user_endpoint():
    db = _FakeDB()
    await _seed(db, "openai", "gpt-x")
    cfgs = await resolve_user_runtime_llm_configs("u1", db)
    assert cfgs.openai.user_endpoint is True


@pytest.mark.asyncio
async def test_user_anthropic_helper_card_is_marked_user_endpoint():
    db = _FakeDB()
    await _seed(db, "anthropic", "claude-haiku-4-5")
    cfgs = await resolve_user_runtime_llm_configs("u1", db)
    assert cfgs.anthropic_helper is not None
    assert cfgs.anthropic_helper.user_endpoint is True


@pytest.mark.asyncio
async def test_platform_helper_card_is_not_marked():
    db = _FakeDB()
    await _seed(db, "openai", "gpt-x", source="netmind_free")
    cfgs = await resolve_user_runtime_llm_configs("u1", db)
    assert cfgs.openai.user_endpoint is False


# ── SDK client construction ──

def _cloud(monkeypatch, on):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud" if on else "local")


def _http_client_of(sdk_client):
    return sdk_client._client  # openai / anthropic both keep the httpx client here


@pytest.mark.parametrize("user_endpoint,cloud,pinned", [
    (True, True, True),
    (False, True, False),   # platform card: our internal gateway stays reachable
    (True, False, False),   # local: unchanged SDK default
])
def test_openai_helper_client(monkeypatch, user_endpoint, cloud, pinned):
    from narranexus.platform.agent_framework.adapters.openai_agents import build_helper_openai_client

    _cloud(monkeypatch, cloud)
    client = build_helper_openai_client(
        OpenAIConfig(api_key="k", base_url="https://llm.example.com/v1", user_endpoint=user_endpoint)
    )
    http = _http_client_of(client)
    assert isinstance(http._transport, PublicOnlyTransport) is pinned
    assert http.follow_redirects is (not pinned)


@pytest.mark.parametrize("user_endpoint,cloud,pinned", [
    (True, True, True),
    (False, True, False),
    (True, False, False),
])
def test_anthropic_helper_client(monkeypatch, user_endpoint, cloud, pinned):
    from narranexus_plugins.llm_clients import anthropic_helper as mod

    _cloud(monkeypatch, cloud)
    monkeypatch.setattr(
        mod, "anthropic_helper_config",
        AnthropicHelperConfig(api_key="k", base_url="https://llm.example.com", user_endpoint=user_endpoint),
    )
    http = _http_client_of(mod.AnthropicHelperSDK._build_client())
    assert isinstance(http._transport, PublicOnlyTransport) is pinned
    assert http.follow_redirects is (not pinned)


@pytest.mark.asyncio
async def test_cloud_user_helper_refuses_rebinding_at_connect(monkeypatch):
    # End to end through the real openai SDK: the stored base_url resolves
    # public, the dial would see the metadata IP -> refused, no socket opened.
    from narranexus.platform.agent_framework.adapters.openai_agents import build_helper_openai_client
    from narranexus.platform.utils.url_safety import UnsafeUrlError

    _cloud(monkeypatch, True)

    async def _metadata(host, port):
        return ["169.254.169.254"]

    monkeypatch.setattr(url_safety, "_default_resolver", _metadata)
    dials = []

    async def _spy(self, host, port, timeout=None, local_address=None, socket_options=None):
        dials.append((host, port))
        raise httpcore.ConnectError("test: sockets disabled")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", _spy)

    client = build_helper_openai_client(
        OpenAIConfig(api_key="k", base_url="https://rebind.example/v1", user_endpoint=True)
    )
    # The SDK wraps transport errors as APIConnectionError ("Connection
    # error." — no address leaks); the cause is the pin's refusal.
    with pytest.raises(openai.APIConnectionError) as exc:
        await client.with_options(max_retries=0).models.list()
    assert isinstance(exc.value.__cause__, UnsafeUrlError)
    assert dials == []


def test_user_endpoint_survives_the_executor_config_round_trip():
    # The orchestrator ships resolved configs to the executor as dicts; a lost
    # flag would run a user card unpinned there.
    import contextvars

    from narranexus.platform.agent_framework.api_config import (
        ClaudeConfig,
        anthropic_helper_config,
        clear_user_config,
        openai_config,
        set_user_config,
    )
    from narranexus.platform.agent_runtime.executor_protocol import (
        apply_provider_configs,
        serialize_provider_configs,
    )

    def _run():
        set_user_config(
            claude=ClaudeConfig(),
            openai=OpenAIConfig(api_key="k", base_url="https://llm.example.com/v1", user_endpoint=True),
            anthropic_helper=AnthropicHelperConfig(api_key="k", user_endpoint=True),
        )
        payload = serialize_provider_configs()

        def _executor_side():
            clear_user_config()  # nothing inherited: only the payload counts
            assert openai_config.user_endpoint is False
            apply_provider_configs(payload)
            return openai_config.user_endpoint, anthropic_helper_config.user_endpoint

        return contextvars.copy_context().run(_executor_side)

    assert contextvars.copy_context().run(_run) == (True, True)
