"""
@file_name: decision_keys.py
@author: Bin Liang
@date: 2026-11-26
@description: Per-agent decision-model keys issued by the free-tier gateway.

In the cloud, narrative routing asks the decision model through the LiteLLM
gateway with one capped key PER AGENT, so the upstream key stays in the
gateway and one hammered agent cannot run up the bill. ``quota-api`` hands the
key out (idempotently) together with the gateway address to ask with it; this
module caches both per process and offers them to the generic resolver as an
`AgentScopedKey`. The address always comes from quota-api, never from the
deployment's LLM gateway setting: that is the sandbox proxy, which refuses the
decision route by design.

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
from dataclasses import dataclass, replace
from typing import Callable, Optional

from loguru import logger

from narranexus.platform.agent_framework.llm.decision_model import AgentScopedKey
from narranexus.platform.integrations.free_tier.wallet_client import (
    DecisionKey,
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
#: A trusted key is re-checked with quota-api after this long, so a key the
#: operator blocked, unblocked or re-issued is noticed without waiting for the
#: gateway to answer 401/403.
_MAX_KEY_AGE_S = 1800.0
#: Entries are ~100 bytes; the bound only stops a long-lived process from
#: keeping every agent it ever routed. An evicted agent costs one more fetch.
_MAX_CACHED_AGENTS = 4096


@dataclass(frozen=True)
class _Entry:
    #: None: quota-api refused this agent (a per-agent 4xx).
    grant: Optional[DecisionKey]
    #: quota-api is asked about this agent again from this clock value on
    #: (the key keeps being served until then, and after, until it answers).
    recheck_at: float
    #: The gateway refused ``grant``; set by the first refusal only.
    rejected: bool = False


class _Unavailable:
    """quota-api itself could not answer (vs refusing this agent)."""


_UNAVAILABLE = _Unavailable()


class DecisionKeyIssuer:
    """agent_id -> that agent's gateway key and address: one quota-api call
    per agent per key age, not per turn. Concurrent turns of one agent may
    each ask; the endpoint is idempotent, so that costs a duplicate call,
    never a second key."""

    def __init__(
        self,
        wallet: WalletClient,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._wallet = wallet
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        #: Service-level pause: no quota-api call at all before this.
        self._paused_until = 0.0

    async def key_for(self, agent_id: str) -> Optional[AgentScopedKey]:
        """The agent's key, or None when it has none right now. A key it has
        is served even while due for a re-check: an agent holding a key
        never falls through to another credential."""
        if not agent_id:
            return None
        entry = self._entries.get(agent_id)
        if entry is not None:
            self._entries.move_to_end(agent_id)
            if self._may_ask(entry.recheck_at):
                entry = await self._recheck(agent_id, entry)
        elif self._may_ask(0.0):
            entry = await self._first_fetch(agent_id)
        if entry is None or entry.grant is None:
            return None
        grant = entry.grant
        return AgentScopedKey(
            grant.base_url, grant.api_key,
            on_rejected=functools.partial(self._rejected, agent_id, grant),
        )

    def _rejected(self, agent_id: str, grant: DecisionKey) -> None:
        """The gateway refused ``grant`` (401/403). Keep sending it (a blocked
        agent must not fall through to another credential) and re-check with
        quota-api once the retry interval has passed, which picks up a
        rotation."""
        entry = self._entries.get(agent_id)
        # Only the first refusal sets the time: later ones must not push the
        # re-check forward, or an agent active every few seconds would never
        # pick up a rotated key.
        if entry is not None and entry.grant == grant and not entry.rejected:
            self._entries[agent_id] = replace(
                entry,
                rejected=True,
                recheck_at=min(entry.recheck_at, self._clock() + _RETRY_AFTER_S),
            )

    def _may_ask(self, recheck_at: float) -> bool:
        now = self._clock()
        return now >= recheck_at and now >= self._paused_until

    async def _first_fetch(self, agent_id: str) -> Optional[_Entry]:
        answer = await self._ask(agent_id)
        if answer is _UNAVAILABLE:
            return None
        if answer is None:
            return self._store(agent_id, _Entry(None, self._clock() + _RETRY_AFTER_S))
        return self._store(agent_id, self._trusted(answer))

    async def _recheck(self, agent_id: str, entry: _Entry) -> _Entry:
        """An entry whose time has come (over age, refused by the gateway, or
        refused by quota-api): the answer replaces it and is trusted for a
        full age — a different key or address is thereby picked up — and any
        failure keeps what we had for another retry interval. Every branch
        stores through `_store`: the entry may have been evicted meanwhile."""
        answer = await self._ask(agent_id)
        if isinstance(answer, DecisionKey):
            return self._store(agent_id, self._trusted(answer))
        current = self._entries.get(agent_id)
        if current is not None and current.grant != entry.grant:
            # A concurrent turn stored a newer answer while this one waited:
            # writing the old grant back would resurrect a dead key.
            return current
        return self._store(
            agent_id,
            replace(current or entry, recheck_at=self._clock() + _RETRY_AFTER_S),
        )

    def _trusted(self, grant: DecisionKey) -> _Entry:
        return _Entry(grant, self._clock() + _MAX_KEY_AGE_S)

    def _store(self, agent_id: str, entry: _Entry) -> _Entry:
        self._entries[agent_id] = entry
        self._entries.move_to_end(agent_id)
        if len(self._entries) > _MAX_CACHED_AGENTS:
            self._entries.popitem(last=False)
        return entry

    async def _ask(self, agent_id: str) -> DecisionKey | None | _Unavailable:
        """The key and address; None when quota-api refused THIS agent; `_UNAVAILABLE`
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
    from narranexus.platform.utils.deployment_mode import is_cloud_mode

    if not is_cloud_mode():
        return None
    wallet = WalletClient.from_settings()
    if wallet is None:
        return None
    return DecisionKeyIssuer(wallet)
