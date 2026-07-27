"""
@file_name: gateway_key_service.py
@author: Bin Liang
@date: 2026-07-23
@description: Mint / revoke / reap per-run LiteLLM gateway session keys ("会话票").

Why this exists
---------------
Free-tier runs used to inject the shared master key straight into the agent
subprocess env, where user-controlled agent logic (`env`, `/proc/self/environ`)
could read and exfiltrate it — a platform-wide blast radius. Instead, the real
master key now lives ONLY inside the LiteLLM gateway container. For each run we
mint a per-run gateway key bound to the user, inject THAT in place of the master
key, and revoke it when the run ends. The ticket only works against our gateway,
is revocable, and — when ``key_max_budget_usd`` is configured — carries a hard
per-key USD ceiling the gateway enforces even on calls we never see, so a leaked
ticket's blast radius is genuinely bounded (not just the master key hidden).

Layer
-----
Minting happens on the BACKEND orchestrator (``open_backend_session``, called
from step 3), NOT in the executor: the executor runs user-controlled agent code
and must never hold the gateway admin key. The backend injects the ticket into
the ClaudeConfig ContextVar so it rides ``provider_configs`` to the executor,
which receives only the scoped, revocable ticket.

Lifecycle & 铁律 alignment
--------------------------
- No wall-clock TTL on the key (`duration` omitted). A run may last hours
  (铁律 #14); the CLI reads the token once at spawn and cannot refresh it, so a
  timed key would guillotine long runs. Validity is bounded by the RUN lifecycle
  instead: revoked in step 3's finally (``BackendGatewaySession.close``), and —
  if a crash orphaned it — by the executor-reaper hook (``revoke_all_for_user``,
  fired only for users the admission controller reports idle, so no live run is
  ever touched).
- Failures never raise into the run and never fall back to the master key (that
  would re-expose it). A mint failure returns None; the caller aborts the run
  with a clean ``gateway_unavailable`` error.

Revocation is by ``key_alias == run_id`` so we never persist the raw secret.
"""
from __future__ import annotations

import dataclasses
import os
import secrets
from dataclasses import dataclass
from typing import List, Optional

import httpx
from loguru import logger

from xyz_agent_context.repository.gateway_session_key_repository import (
    GatewaySessionKeyRepository,
)

_RUN_ID_PREFIX = "sess_"
_DEFAULT_TIMEOUT_S = 10.0


def _parse_float(raw: str) -> Optional[float]:
    """Parse an env string to float, or None on empty/invalid (never raises).

    A non-empty-but-unparseable value (e.g. ``1.0USD``) is logged at WARNING —
    silently dropping it would make the per-key budget cap vanish unnoticed."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            f"[gateway] ignoring unparseable float env value {raw!r} "
            f"(per-key budget cap will be OFF)"
        )
        return None


@dataclass(frozen=True)
class MintedSessionKey:
    run_id: str
    key: str       # the secret injected into the subprocess env — NOT persisted
    base_url: str  # gateway endpoint the agent subprocess talks to


@dataclass
class BackendGatewaySession:
    """Handle for a run's minted session key, held on the BACKEND side.

    The mint happens in the backend orchestrator (step 3), NOT in the executor:
    the executor runs user-controlled agent code and must never hold the gateway
    admin key. The backend injects the minted ticket into the ClaudeConfig
    ContextVar, which then rides ``provider_configs`` to the executor — so the
    executor receives only the scoped, revocable ticket. ``close()`` revokes at
    the end of the run (the lifecycle bound that replaces a wall-clock TTL,
    铁律 #14)."""

    svc: "GatewayKeyService"
    run_id: str

    async def close(self) -> None:
        await self.svc.revoke_session_key(self.run_id)


class GatewayKeyService:
    """Talks to the LiteLLM proxy admin API to issue and delete per-run keys.

    Stateless aside from the ``instance_gateway_session_keys`` ledger; safe to
    construct per use or hold as a singleton.
    """

    def __init__(
        self,
        db,
        *,
        gateway_url: str,
        admin_key: str,
        agent_base_url: Optional[str] = None,
        models: Optional[List[str]] = None,
        key_max_budget_usd: Optional[float] = None,
        request_timeout_s: float = _DEFAULT_TIMEOUT_S,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._db = db
        self._repo = GatewaySessionKeyRepository(db)
        self._gateway_url = (gateway_url or "").rstrip("/")
        self._admin_key = admin_key or ""
        # The subprocess-facing base_url may differ from the admin base_url only
        # by protocol path; default to the gateway root.
        self._agent_base_url = (agent_base_url or gateway_url or "").rstrip("/")
        self._models = models or []
        # Per-key USD spend ceiling enforced BY THE GATEWAY. This is what makes
        # "a leaked ticket is worth at most X" a real bound rather than a claim:
        # our own cost_tracker only meters calls routed through the backend, so a
        # ticket read out of the subprocess and used directly against the gateway
        # would otherwise be uncapped (and, by design, non-expiring for the run,
        # 铁律 #14). None/<=0 → omit (no gateway cap; the master key is still
        # never exposed, but the per-key blast radius is unbounded — set it).
        self._key_max_budget_usd = key_max_budget_usd
        self._timeout = request_timeout_s
        self._transport = transport

    # -- construction ------------------------------------------------------

    @classmethod
    def from_env(cls, db) -> Optional["GatewayKeyService"]:
        """Build from env, or None when the gateway is not configured (e.g. local
        mode). The caller short-circuits the whole gateway path on None."""
        url = os.environ.get("SYSTEM_DEFAULT_LLM_GATEWAY_URL", "").strip()
        admin = os.environ.get("SYSTEM_DEFAULT_LLM_GATEWAY_ADMIN_KEY", "").strip()
        if not (url and admin):
            return None
        agent_base = (
            os.environ.get("SYSTEM_DEFAULT_LLM_ANTHROPIC_BASE_URL", "").strip() or url
        )
        models = [
            m
            for m in (
                os.environ.get("SYSTEM_DEFAULT_LLM_AGENT_MODEL", "").strip(),
                os.environ.get("SYSTEM_DEFAULT_LLM_HELPER_MODEL", "").strip(),
            )
            if m
        ]
        max_budget = _parse_float(
            os.environ.get("SYSTEM_DEFAULT_LLM_GATEWAY_KEY_MAX_BUDGET_USD", "")
        )
        return cls(
            db,
            gateway_url=url,
            admin_key=admin,
            agent_base_url=agent_base,
            models=models,
            key_max_budget_usd=max_budget,
        )

    def is_enabled(self) -> bool:
        return bool(self._gateway_url and self._admin_key)

    # -- lifecycle ---------------------------------------------------------

    async def mint_session_key(
        self,
        user_id: str,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[MintedSessionKey]:
        """Mint one gateway key for a run. Returns None on any failure — the
        caller must treat None as "system tier unavailable" and NEVER fall back
        to the master key."""
        run_id = run_id or (_RUN_ID_PREFIX + secrets.token_hex(4))
        payload = {
            # alias == run_id: the only handle we keep, and how we revoke.
            "key_alias": run_id,
            # Top-level user_id (not just metadata) so LiteLLM aggregates spend
            # per user across their per-run keys — the hook for a future
            # per-user (not just per-key) budget ceiling.
            "user_id": user_id,
            "metadata": {
                "user_id": user_id,
                "agent_id": agent_id or "",
                "run_id": run_id,
            },
            # No "duration": deliberately non-expiring at the gateway; the RUN
            # lifecycle bounds validity (铁律 #14 — see module docstring).
        }
        if self._models:
            payload["models"] = self._models
        if self._key_max_budget_usd and self._key_max_budget_usd > 0:
            # Hard USD ceiling at the gateway. NOTE it binds to the KEY, and keys
            # are per-run — so the real guarantee is "≤ cap PER RUN", which bounds
            # a single leaked ticket even for calls we never see (direct-to-gateway
            # abuse). Per-user cumulative capping would need a gateway user budget
            # (the top-level user_id above is the hook for that next step).
            payload["max_budget"] = self._key_max_budget_usd

        try:
            data = await self._post("/key/generate", payload)
        except Exception as e:  # network / 4xx / 5xx — gateway unavailable
            logger.error(f"[gateway] mint failed for user={user_id}: {e!r}")
            return None

        key = data.get("key")
        if not key:
            logger.error(f"[gateway] mint returned no key for user={user_id}: {data}")
            return None

        # Record the ledger row BEFORE handing the key out, so a crash right
        # after minting still leaves a reapable trace. Only the non-secret token
        # hash is stored; the usable secret is never persisted.
        try:
            await self._repo.create(
                run_id=run_id,
                user_id=user_id,
                agent_id=agent_id,
                key_hash=data.get("token"),
            )
        except Exception as e:
            # Key exists at the gateway but we couldn't track it → it would be an
            # unreapable orphan. Revoke immediately and report mint failure.
            logger.error(
                f"[gateway] ledger write failed; revoking {run_id}: {e!r}"
            )
            await self._delete_alias(run_id, best_effort=True)
            return None

        return MintedSessionKey(run_id=run_id, key=key, base_url=self._agent_base_url)

    async def revoke_session_key(self, run_id: str) -> None:
        """Delete the key at the gateway and mark the ledger row revoked. Never
        raises: revocation is cleanup, and a failure here must not surface as a
        run error. Orphans left by a gateway blip are caught by the reaper."""
        await self._delete_alias(run_id, best_effort=True)
        try:
            await self._repo.mark_revoked(run_id)
        except Exception as e:
            logger.warning(f"[gateway] mark_revoked row failed for {run_id}: {e!r}")

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke every ACTIVE key for a user. Returns the count revoked.

        Called from the executor-reaper hook: the reaper only culls users the
        admission controller reports IDLE (zero active loops, 铁律 #14-safe), so
        at that instant the user has no live run and every ACTIVE key of theirs
        is a crash orphan left when an executor died before its finally ran."""
        try:
            rows = await self._repo.list_active_for_user(user_id)
        except Exception as e:
            logger.warning(f"[gateway] list_active_for_user failed for {user_id}: {e!r}")
            return 0
        for r in rows:
            await self.revoke_session_key(r.run_id)
        if rows:
            logger.info(
                f"[gateway] revoked {len(rows)} orphan key(s) for idle user={user_id}"
            )
        return len(rows)

    # -- HTTP --------------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._admin_key}"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def _post(self, path: str, payload: dict) -> dict:
        async with self._client() as client:
            resp = await client.post(
                self._gateway_url + path, json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            return resp.json()

    async def _delete_alias(self, run_id: str, *, best_effort: bool = False) -> None:
        try:
            await self._post("/key/delete", {"key_aliases": [run_id]})
        except Exception as e:
            if best_effort:
                logger.warning(f"[gateway] delete at gateway failed for {run_id}: {e!r}")
                return
            raise


async def open_backend_session(
    db, *, agent_id: Optional[str] = None
) -> tuple[Optional[BackendGatewaySession], bool]:
    """Backend orchestrator entry point (step 3): for a system-tier run, mint a
    per-run gateway session key and inject it into the CURRENT task's
    ``ClaudeConfig`` ContextVar so it rides ``provider_configs`` to the executor.

    Returns ``(session, ok)``:
      * non-system run  -> ``(None, True)``   no-op, run proceeds normally
      * minted+injected -> ``(session, True)`` caller must ``await session.close()``
                           in a finally to revoke at run end
      * mint impossible -> ``(None, False)``   caller MUST abort the run — never
                           fall back to the master key (the leak we removed) and
                           never spawn with the empty placeholder key

    Runs in the BACKEND (holds the gateway admin key; no user code). The executor
    never mints and never sees the admin key — it only receives the scoped ticket.
    """
    # Local imports: avoid an import cycle and keep non-system runs cheap.
    from xyz_agent_context.agent_framework.api_config import (
        ClaudeConfig,
        OpenAIConfig,
        get_current_user_id,
        get_provider_source,
        set_user_config,
        snapshot_user_config,
    )

    if get_provider_source() != "system":
        return None, True

    svc = GatewayKeyService.from_env(db)
    if svc is None:
        # provider_source=="system" but no gateway configured → misconfig.
        # Do NOT spawn with the empty placeholder key.
        logger.error("[gateway] system run but gateway not configured; aborting")
        return None, False

    minted = await svc.mint_session_key(
        user_id=get_current_user_id() or "unknown", agent_id=agent_id
    )
    if minted is None:
        return None, False

    # Overlay the ticket onto the claude config; leave the other slots as-is.
    # The helper (openai) slot keeps its backend gateway key from the resolver.
    snap = snapshot_user_config()
    claude = snap.get("claude") or ClaudeConfig()
    new_claude = dataclasses.replace(
        claude, api_key=minted.key, base_url=minted.base_url
    )
    set_user_config(
        claude=new_claude,
        openai=snap.get("openai") or OpenAIConfig(),
        codex=snap.get("codex"),
        anthropic_helper=snap.get("anthropic_helper"),
        cli_helper=snap.get("cli_helper"),
    )
    logger.info(
        f"[gateway] minted session key run_id={minted.run_id} "
        f"base_url={minted.base_url} (backend-side, ships to executor)"
    )
    return BackendGatewaySession(svc=svc, run_id=minted.run_id), True
