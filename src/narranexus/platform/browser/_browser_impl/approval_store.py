"""
@file_name: approval_store.py
@author:
@date: 2026-09-22
@description: Pending privileged-capability approvals shared across processes.

The MCP host owns browser sessions; the backend serves approval requests to the
UI. Database storage makes pending requests and decisions visible to both
processes, so an agent cannot wait for an invisible in-memory prompt.

Ordinary browsing never creates requests. Only supported privileged capabilities
are listed or resolved; persisted requests for retired capabilities are inert.
Decision persistence failures propagate so the route can report a retryable error without consuming
the request or claiming the answer reached the other process.
"""
from __future__ import annotations

import json
import uuid
import asyncio
from typing import Any, Optional

from loguru import logger

from narranexus.platform.browser._browser_impl.approvals import (
    APPROVABLE_CAPABILITIES, ApprovalRegistry, PendingApproval, allowed_lifetimes, validate_request,
)
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
from narranexus.platform.utils.timezone import utc_now

TABLE = "instance_browser_approvals"


def _public(row: dict) -> dict:
    """The shape the UI addresses approvals by.

    `id` is the APPROVAL id, never the table's auto-increment primary key.
    Handing out the raw row sent every decision to
    `/api/browser/approvals/1` — a request that matched nothing, so the prompt
    stayed on screen and clicking it did nothing at all.
    """
    return {
        "id": row.get("approval_id"),
        "agent_id": row.get("agent_id"),
        "origin": row.get("origin"),
        "capability": row.get("capability"),
        "requested_at": row.get("requested_at"),
        "allowed_lifetimes": allowed_lifetimes(row.get("turn_id") or "", row.get("thread_id") or ""),
    }


class ApprovalStore:
    """Cross-process pending approvals. One row per unanswered question."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def request(
        self,
        *,
        agent_id: str,
        origin: str,
        capability: str,
        turn_id: str,
        thread_id: str,
    ) -> Optional[dict]:
        """Record (or find) the question. Returns the row, or None on failure.

        Raises:
            ValueError: for a capability that must never be prompted for.
                ``full_cdp_access`` is configured deliberately (design §6);
                a prompt for it would let one click grant the one permission
                with no ceiling.
        """
        validate_request(origin, capability)
        filters = {"agent_id": agent_id, "origin": origin, "capability": capability,
                   "turn_id": turn_id, "thread_id": thread_id}
        try:
            policy_row = await self._db.get_one("instance_browser_policies", {"agent_id": agent_id})
            receipts = set(json.loads(policy_row["policy_json"]).get("approval_receipts", [])) if policy_row else set()
            # A settings reset can require a fresh answer in the same scope.
            # Advance deterministically past consumed IDs so concurrent asks
            # still deduplicate without resurrecting a revoked prompt.
            seed = json.dumps(filters, sort_keys=True)
            approval_id = f"appr_{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex}"
            while approval_id in receipts:
                approval_id = f"appr_{uuid.uuid5(uuid.NAMESPACE_URL, approval_id).hex}"
            existing = await self._db.get_one(
                TABLE,
                {"approval_id": approval_id},
            )
            if existing:
                return existing

            row = {
                # The existing unique approval_id index deduplicates concurrent
                # requests across processes without a new schema constraint.
                "approval_id": approval_id,
                "agent_id": agent_id,
                "origin": origin,
                "capability": capability,
                "turn_id": turn_id,
                "thread_id": thread_id,
                "requested_at": utc_now().isoformat(),
            }
            try:
                await self._db.insert(TABLE, row)
            except Exception:
                existing = await self._db.get_one(TABLE, {"approval_id": approval_id})
                if existing:
                    return existing
                raise
            return row
        except Exception:
            # The caller retains its refusal if recording the prompt fails.
            logger.exception("could not record a browser approval request")
            return None

    async def pending(self, agent_id: str) -> list[dict]:
        """Unanswered questions for one agent, oldest first."""
        try:
            rows = await self._db.get(
                TABLE, {"agent_id": agent_id}, order_by="requested_at"
            )
        except Exception:
            logger.exception("could not read browser approvals")
            return []
        policy_row = await self._db.get_one("instance_browser_policies", {"agent_id": agent_id})
        receipts = set(json.loads(policy_row["policy_json"]).get("approval_receipts", [])) if policy_row and policy_row.get("policy_json") else set()
        return [_public(row) for row in (rows or [])
                if row["capability"] in APPROVABLE_CAPABILITIES and row["approval_id"] not in receipts]

    async def take(self, approval_id: str) -> Optional[dict]:
        """Claim a question for answering, removing it atomically-enough.

        Delete-then-return rather than read-then-delete: two tabs showing the
        same prompt must not both apply a decision, and the second caller
        getting None is how the UI learns the question is already settled.
        """
        try:
            row = await self.get(approval_id)
            if not row:
                return None
            removed = await self._db.delete(TABLE, {"approval_id": approval_id})
            if not removed:
                # Someone else took it between the read and the delete.
                return None
            return row
        except Exception:
            logger.exception("could not claim a browser approval")
            return None

    async def get(self, approval_id: str) -> Optional[dict]:
        """Read without consuming so routes can authorize before any mutation."""
        row = await self._db.get_one(TABLE, {"approval_id": approval_id})
        return row if row and row["capability"] in APPROVABLE_CAPABILITIES else None

    async def resolve(
        self, approval_id: str, *, agent_id: str, decision: str, lifetime: str
    ) -> bool:
        """Atomically save a decision and receipt, then retire its pending row.

        Compare-and-swap works on SQLite's shared connection as well as MySQL.
        A process crash after saving cannot replay the decision: its receipt is
        part of the same JSON update, and pending() hides consumed prompts.
        """
        policy_table = "instance_browser_policies"
        while True:
            row = await self.get(approval_id)
            if row is None or row["agent_id"] != agent_id:
                return False
            pending = PendingApproval(
                id=row["approval_id"], agent_id=agent_id, origin=row["origin"],
                capability=row["capability"], turn_id=row.get("turn_id") or "",
                thread_id=row.get("thread_id") or "",
            )
            raw = await self._db.get_one(policy_table, {"agent_id": agent_id})
            # Invalid policy JSON is an error, never an absent policy that an
            # approval could overwrite with a wider permission document.
            policy = BrowserPolicy.from_dict(json.loads(raw["policy_json"])) if raw else BrowserPolicy()
            if approval_id in policy._approval_receipts:
                return False
            registry = ApprovalRegistry()
            registry._by_id[pending.id] = pending
            if not registry.resolve(pending.id, decision=decision, lifetime=lifetime, policy=policy):
                return False
            if raw is None:
                now = utc_now().isoformat()
                try:
                    await self._db.insert(policy_table, {
                        "agent_id": agent_id, "policy_json": json.dumps(BrowserPolicy().to_dict()),
                        "created_at": now, "updated_at": now,
                    })
                except Exception:
                    if await self._db.get_one(policy_table, {"agent_id": agent_id}) is None:
                        raise
                continue
            policy._approval_receipts.add(approval_id)
            written = await self._db.execute(
                "UPDATE instance_browser_policies SET policy_json = %s, updated_at = %s "
                "WHERE BINARY agent_id = BINARY %s AND BINARY policy_json = BINARY %s "
                "AND EXISTS (SELECT 1 FROM instance_browser_approvals "
                "WHERE BINARY approval_id = BINARY %s AND BINARY agent_id = BINARY %s)",
                (json.dumps(policy.to_dict()), utc_now().isoformat(), agent_id,
                 raw["policy_json"], approval_id, agent_id), fetch=False,
            )
            if written:
                try:
                    await self._db.delete(TABLE, {"approval_id": approval_id, "agent_id": agent_id})
                except Exception:
                    logger.exception("approved browser prompt retained; receipt prevents replay")
                return True
            await asyncio.sleep(0)

    async def clear_for_agent(self, agent_id: str) -> None:
        """Drop an agent's outstanding questions (its browser closed)."""
        try:
            await self._db.delete(TABLE, {"agent_id": agent_id})
        except Exception:
            logger.exception("could not clear browser approvals")
