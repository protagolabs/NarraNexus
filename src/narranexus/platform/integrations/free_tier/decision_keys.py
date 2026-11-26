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

Never raises and never blocks a turn for long: when no key can be had the
resolver falls through to the next credential source. An agent that HAS a key
keeps it, even after the gateway rejected it: a revoked agent must stay on its
own (refused) key, never fall through to an uncapped credential.
"""
from __future__ import annotations

import asyncio
import functools
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger

from narranexus.platform.agent_framework.llm.decision_model import AgentScopedKey
from narranexus.platform.integrations.free_tier.wallet_client import (
    WalletClient,
    WalletError,
    WalletRejected,
)

#: Wall for the quota-api round trip, paid only when quota-api is asked and on
#: top of the decision call's own timeout.
_FETCH_TIMEOUT_S = 2.0
#: How long a failure is remembered before quota-api is asked again: a down
#: or older quota-api must not cost every turn the fetch timeout, and a
#: rejected key is re-checked at most once per interval.
_RETRY_AFTER_S = 60.0
#: Entries are ~100 bytes; the bound only stops a long-lived process from
#: keeping every agent it ever routed. An evicted agent costs one more fetch.
_MAX_CACHED_AGENTS = 4096


@dataclass
class _Entry:
    #: None: quota-api refused this agent (a per-agent 4xx).
    key: Optional[str]
    #: 0 while the key is trusted; otherwise quota-api is not asked about
    #: this agent before this clock value.
    recheck_at: float = 0.0


class _Unavailable:
    """quota-api itself could not answer (vs refusing this agent)."""


_UNAVAILABLE = _Unavailable()


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
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        #: Service-level pause: no quota-api call at all before this.
        self._paused_until = 0.0

    async def key_for(self, agent_id: str) -> Optional[AgentScopedKey]:
        """The agent's key, or None when it has none right now."""
        if not agent_id:
            return None
        entry = self._entries.get(agent_id)
        if entry is not None:
            self._entries.move_to_end(agent_id)
            if entry.recheck_at and self._may_ask(entry.recheck_at):
                entry = await self._recheck(agent_id, entry)
        elif self._may_ask(0.0):
            entry = await self._first_fetch(agent_id)
        if entry is None or entry.key is None:
            return None
        return AgentScopedKey(
            self._base_url, entry.key,
            on_rejected=functools.partial(self._rejected, agent_id, entry.key),
        )

    def _rejected(self, agent_id: str, key: str) -> None:
        """The gateway answered 401 for ``key``. Keep sending it (a blocked
        agent must not fall through to another credential) and re-check with
        quota-api once the interval has passed, which picks up a rotation."""
        entry = self._entries.get(agent_id)
        # Only the first 401 sets the time: later ones must not push the
        # re-check forward, or an agent active every few seconds would never
        # pick up a rotated key.
        if entry is not None and entry.key == key and not entry.recheck_at:
            entry.recheck_at = self._clock() + _RETRY_AFTER_S

    def _may_ask(self, recheck_at: float) -> bool:
        now = self._clock()
        return now >= recheck_at and now >= self._paused_until

    async def _first_fetch(self, agent_id: str) -> Optional[_Entry]:
        answer = await self._ask(agent_id)
        if answer is _UNAVAILABLE:
            return None
        return self._store(
            agent_id,
            _Entry(answer) if answer is not None
            else _Entry(None, self._clock() + _RETRY_AFTER_S),
        )

    async def _recheck(self, agent_id: str, entry: _Entry) -> _Entry:
        """A rejected key or a refused agent, once its interval has passed:
        a new key replaces the entry, the same key is trusted again, and any
        failure keeps what we had for another interval."""
        answer = await self._ask(agent_id)
        if isinstance(answer, str):
            if answer != entry.key:
                # Through the bound: the entry may have been evicted while
                # quota-api was being asked.
                entry = self._store(agent_id, _Entry(answer))
            else:
                entry.recheck_at = 0.0
        else:
            entry.recheck_at = self._clock() + _RETRY_AFTER_S
        return entry

    def _store(self, agent_id: str, entry: _Entry) -> _Entry:
        self._entries[agent_id] = entry
        self._entries.move_to_end(agent_id)
        if len(self._entries) > _MAX_CACHED_AGENTS:
            self._entries.popitem(last=False)
        return entry

    async def _ask(self, agent_id: str) -> str | None | _Unavailable:
        """The key; None when quota-api refused THIS agent; `_UNAVAILABLE`
        (and a service-level pause) when quota-api could not answer at all."""
        try:
            async with asyncio.timeout(_FETCH_TIMEOUT_S):
                return await self._wallet.decision_key(agent_id)
        except WalletRejected as e:
            # About this agent only (e.g. a malformed id): others are still asked.
            _log_failure(agent_id, "refused this agent", e)
            return None
        except (WalletError, TimeoutError) as e:
            self._paused_until = self._clock() + _RETRY_AFTER_S
            _log_failure(agent_id, "unavailable", e)
            return _UNAVAILABLE


def _log_failure(agent_id: str, outcome: str, error: Exception) -> None:
    # The wallet client's messages carry no key and no response body.
    logger.warning(
        f"[DecisionKeys] quota-api {outcome} for {agent_id}: "
        f"{type(error).__name__}: {error} — an agent holding a key keeps it, "
        f"others use the next credential source; not asked again for "
        f"{_RETRY_AFTER_S:.0f}s"
    )


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
