"""
@file_name: decision_keys.py
@author: Bin Liang
@date: 2026-11-26
@description: Per-agent decision-model keys issued by the free-tier gateway.

In the cloud, narrative routing asks the decision model through the LiteLLM
gateway with one capped key PER AGENT, so the upstream key stays in the
gateway and one hammered agent cannot run up the bill. ``quota-api`` hands the
key out (idempotently); this module caches it per process and offers it to the
generic resolver as an `AgentScopedKey`.

Never raises and never blocks a turn for long: a failure to get a key returns
None and the resolver falls through to the next credential source.
"""
from __future__ import annotations

import asyncio
import functools
import time
from collections import OrderedDict
from typing import Callable, Optional

from loguru import logger

from narranexus.platform.agent_framework.llm.decision_model import AgentScopedKey
from narranexus.platform.integrations.free_tier.wallet_client import (
    WalletClient,
    WalletError,
)

#: Wall for the quota-api round trip, paid only on a cold cache and on top of
#: the decision call's own timeout.
_FETCH_TIMEOUT_S = 2.0
#: After a failed fetch, quota-api is not asked again for this long: a down or
#: older (no endpoint) quota-api must not cost every turn the fetch timeout.
_RETRY_AFTER_S = 60.0
#: Keys are ~80 bytes; the bound only stops a long-lived process from keeping
#: every agent it ever routed. An evicted agent just costs one more fetch.
_MAX_CACHED_AGENTS = 4096


class DecisionKeyIssuer:
    """agent_id -> that agent's gateway key, one quota-api call per agent per
    process. Concurrent first turns of one agent may each fetch; the endpoint
    is idempotent, so that costs a duplicate call, never a second key."""

    def __init__(
        self,
        wallet: WalletClient,
        gateway_base_url: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._wallet = wallet
        self._base_url = gateway_base_url
        self._clock = clock
        self._keys: OrderedDict[str, str] = OrderedDict()
        self._retry_at = 0.0

    async def key_for(self, agent_id: str) -> Optional[AgentScopedKey]:
        """The agent's key, or None when none can be had right now."""
        if not agent_id:
            return None
        key = self._keys.get(agent_id)
        if key is not None:
            self._keys.move_to_end(agent_id)
        else:
            key = await self._fetch(agent_id)
            if key is None:
                return None
            self._keys[agent_id] = key
            if len(self._keys) > _MAX_CACHED_AGENTS:
                self._keys.popitem(last=False)
        return AgentScopedKey(
            self._base_url, key, on_rejected=functools.partial(self.forget, agent_id, key)
        )

    def forget(self, agent_id: str, key: str) -> None:
        """Drop a key the gateway rejected (rotated or deleted), so the next
        turn re-fetches. Only that key: a fresher one fetched meanwhile stays."""
        if self._keys.get(agent_id) == key:
            del self._keys[agent_id]

    async def _fetch(self, agent_id: str) -> Optional[str]:
        if self._clock() < self._retry_at:
            return None
        try:
            async with asyncio.timeout(_FETCH_TIMEOUT_S):
                return await self._wallet.decision_key(agent_id)
        except (WalletError, TimeoutError) as e:
            self._retry_at = self._clock() + _RETRY_AFTER_S
            # The wallet client's messages carry no key and no response body.
            logger.warning(
                f"[DecisionKeys] no gateway key for {agent_id}: "
                f"{type(e).__name__}: {e} — next credential source; quota-api "
                f"not asked again for {_RETRY_AFTER_S:.0f}s"
            )
            return None


@functools.cache
def decision_key_issuer() -> Optional[DecisionKeyIssuer]:
    """The process's issuer, or None when this deployment has no free-tier
    gateway (local/desktop, or a cloud deploy without the wallet service).
    One instance per process so its cache outlives a turn."""
    from narranexus.platform.agent_framework.providers.free_tier import (
        free_tier_endpoints,
    )
    from narranexus.platform.utils.deployment_mode import is_cloud_mode

    if not is_cloud_mode():
        return None
    wallet = WalletClient.from_settings()
    if wallet is None:
        return None
    return DecisionKeyIssuer(wallet, free_tier_endpoints().openai_base_url)
