"""
@file_name: gateway_spend_reconciler.py
@author: Bin Liang
@date: 2026-07-27
@description: Meter free-tier AGENT token usage from the LiteLLM gateway.

Why this exists
---------------
The free-tier agent runs on a proxied non-Anthropic model (the LiteLLM gateway)
whose Claude Code CLI reports usage 0 for every field — so the agent loop's own
(large) token consumption was never recorded and the per-user quota only ever
moved by tiny helper-LLM amounts. The gateway itself, sitting in the request
path, records the REAL prompt/completion tokens per request (LiteLLM SpendLogs).

This background worker periodically finds finished runs (revoked session keys)
that haven't been metered yet, sums that run's usage from the gateway
(`/spend/logs?api_key=<key_hash>`), and deducts it from the user's quota. Only
the agent's per-run keys live in `instance_gateway_session_keys`; the helper
(backend key) is already metered by cost_tracker — so there's no double-count.

- **Cloud + gateway only** — no-op otherwise.
- **Idempotent** — `instance_gateway_session_keys.metered_at` guards against
  double-charging; a fetch failure leaves the run unmetered to retry next cycle.
- **Decoupled from run timing** — reconciles runs revoked > flush-grace ago, so
  LiteLLM's async SpendLog writes have flushed (no reliance on the run finishing
  exactly when we look). Never force-stops anything (铁律 #14).
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

from loguru import logger

DEFAULT_INTERVAL_SEC = 120
# Only meter runs revoked at least this long ago, so LiteLLM's batched SpendLog
# writes have flushed and the run's last request is counted.
DEFAULT_FLUSH_GRACE_SEC = 120


class GatewaySpendReconciler:
    def __init__(
        self,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SEC,
        flush_grace_seconds: float = DEFAULT_FLUSH_GRACE_SEC,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.flush_grace_seconds = flush_grace_seconds

    async def reconcile_once(self, *, db=None, svc=None) -> int:
        """One pass. Returns the number of runs metered.

        `db` / `svc` are injectable for tests; in production both are resolved
        from the process db-factory singleton and the environment.
        """
        from xyz_agent_context.agent_framework.api_config import (
            set_current_user_id,
            set_provider_source,
        )
        from xyz_agent_context.agent_framework.providers.gateway_key_service import (
            GatewayKeyService,
        )
        from xyz_agent_context.repository.gateway_session_key_repository import (
            GatewaySessionKeyRepository,
        )
        from xyz_agent_context.utils.cost_tracker import record_cost
        from xyz_agent_context.utils.db.db_factory import get_db_client

        if db is None:
            db = await get_db_client()
        if svc is None:
            svc = GatewayKeyService.from_env(db)
        if svc is None:
            return 0
        repo = GatewaySessionKeyRepository(db)
        try:
            runs = await repo.list_unmetered_revoked(int(self.flush_grace_seconds))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[spend-reconciler] list_unmetered_revoked failed: {e!r}")
            return 0

        metered = 0
        for run in runs:
            usage = await svc.fetch_run_usage(run.key_hash) if run.key_hash else None
            if usage is None:
                # Fetch failed (or no key_hash) → leave unmetered, retry next cycle.
                continue
            inp, out, model = usage
            try:
                if inp > 0 or out > 0:
                    # Same path as step_4.6: provider_source=system + user_id in
                    # ContextVars make record_cost write the cost_records row AND
                    # deduct the quota. Reset after so the worker task stays clean.
                    set_provider_source("system")
                    set_current_user_id(run.user_id)
                    try:
                        await record_cost(
                            db=db,
                            agent_id=run.agent_id or "",
                            event_id=None,
                            call_type="agent_loop",
                            model=model or "gateway-agent",
                            input_tokens=inp,
                            output_tokens=out,
                        )
                    finally:
                        set_provider_source(None)
                        set_current_user_id(None)
                # Mark metered even when usage is 0 (errored run) so we don't
                # re-scan it forever.
                await repo.mark_metered(run.run_id)
                metered += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[spend-reconciler] meter failed run={run.run_id}: {e!r}")

        if metered:
            logger.info(f"[spend-reconciler] metered {metered} finished run(s)")
        return metered

    async def run_forever(self) -> None:
        logger.info(
            f"[spend-reconciler] started (interval={self.interval_seconds}s "
            f"grace={self.flush_grace_seconds}s)"
        )
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[spend-reconciler] cycle error: {e!r}")


def _on_done(task: "asyncio.Task") -> None:
    # Incident lesson #2: a fire-and-forget task must surface its death.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"[spend-reconciler] background task died: {exc!r}")


def maybe_start_gateway_spend_reconciler() -> Optional["asyncio.Task"]:
    """Start the reconciler as a background task — cloud + gateway only.

    No-op (returns None) when the free-tier gateway isn't configured (local /
    desktop, or gateway disabled): there is nothing to meter there.
    """
    if not os.environ.get("SYSTEM_DEFAULT_LLM_GATEWAY_URL", "").strip():
        return None
    interval = float(os.getenv("GATEWAY_SPEND_RECONCILE_INTERVAL_SEC", "") or DEFAULT_INTERVAL_SEC)
    grace = float(os.getenv("GATEWAY_SPEND_FLUSH_GRACE_SEC", "") or DEFAULT_FLUSH_GRACE_SEC)
    reconciler = GatewaySpendReconciler(interval_seconds=interval, flush_grace_seconds=grace)
    task = asyncio.create_task(reconciler.run_forever())
    task.add_done_callback(_on_done)
    return task
