"""
@file_name: approvals.py
@author:
@date: 2026-09-22
@description: Resolve independent privileged-capability approval requests.

Website access is not a capability in this registry. For other capabilities,
``decide()`` can say "ask", but on its own that is a dead end: the agent
reports ``NEEDS_HUMAN`` and nothing the user does changes the answer. This
module is the missing half — it records the question, shows it to the user,
and applies their answer to the live policy.

Everything here is shaped by one concern: **not training people to click yes.**

* A request names the exact origin and capability. "A website wants access" is
  a prompt nobody can evaluate, so nobody reads it.
* Asking twice for the same thing reuses the request rather than stacking two
  identical prompts to dismiss.
* "For this conversation" stays that. A lifetime that quietly becomes
  permanent is how a considered yes turns into a blanket one.
* An approval can never override a configured deny, and ``full_cdp_access`` is
  never grantable here at all (design §6) — the one permission with no ceiling
  has to be configured deliberately, not clicked through.

This registry owns pure decision logic. The cross-process approval store
instantiates it against the current policy and persists the result atomically.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

from narranexus.platform.browser._browser_impl.policy import (
    _NOT_GRANTABLE,
    BrowserPolicy,
    Capability,
    OriginPolicy,
    decide,
    origin_of,
)

#: How long an approval lasts. ``always`` is written into the policy itself so
#: it survives a restart; the other two are session grants that expire.
ApprovalLifetime = Literal["turn", "thread", "always"]
Decision = Literal["allow", "deny"]

_LIFETIMES = ("turn", "thread", "always")
APPROVABLE_CAPABILITIES = frozenset({"uploads", "downloads", "auto_review"})


def allowed_lifetimes(turn_id: str, thread_id: str) -> list[str]:
    return [*( ["turn"] if turn_id else []), *( ["thread"] if thread_id else []), "always"]


def validate_request(origin: str, capability: str) -> None:
    if capability not in APPROVABLE_CAPABILITIES:
        raise ValueError(f"{capability} is not approvable through a prompt")
    if origin_of(origin) != origin:
        raise ValueError("approval requires a canonical HTTP origin")


@dataclass(frozen=True)
class PendingApproval:
    """One unanswered question, with everything the prompt needs to be specific."""

    id: str
    agent_id: str
    origin: str
    capability: str
    turn_id: str
    thread_id: str
    requested_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "origin": self.origin,
            "capability": self.capability,
            "requested_at": self.requested_at,
            "allowed_lifetimes": allowed_lifetimes(self.turn_id, self.thread_id),
        }


class ApprovalRegistry:
    """Outstanding questions, keyed so the same one is never asked twice."""

    def __init__(self) -> None:
        self._by_id: dict[str, PendingApproval] = {}

    # ── raising ──────────────────────────────────────────────────────────

    def request(
        self,
        *,
        agent_id: str,
        origin: str,
        capability: Capability,
        turn_id: str,
        thread_id: str,
    ) -> PendingApproval:
        """Record (or find) the question. Idempotent per agent+origin+capability.

        Raises:
            ValueError: for a capability that must never be clicked through.
                ``resolve(..., lifetime="always")`` writes *configuration*, so
                gating only the session-grant path left a way in: raise the
                prompt, answer "always", and the ungated capability is
                configured by one click. The prompt must not exist.
        """
        validate_request(origin, capability)
        for pending in self._by_id.values():
            if (
                pending.agent_id == agent_id
                and pending.origin == origin
                and pending.capability == capability
                and pending.turn_id == turn_id
                and pending.thread_id == thread_id
            ):
                return pending

        pending = PendingApproval(
            id=f"appr_{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            origin=origin,
            capability=capability,
            turn_id=turn_id,
            thread_id=thread_id,
        )
        self._by_id[pending.id] = pending
        return pending

    def pending(self, agent_id: str) -> list[PendingApproval]:
        """Unanswered questions for one agent, oldest first."""
        items = [p for p in self._by_id.values() if p.agent_id == agent_id]
        return sorted(items, key=lambda p: p.requested_at)

    def get(self, approval_id: str) -> Optional[PendingApproval]:
        return self._by_id.get(approval_id)

    # ── answering ────────────────────────────────────────────────────────

    def resolve(
        self,
        approval_id: str,
        *,
        decision: Decision,
        lifetime: ApprovalLifetime,
        policy: BrowserPolicy,
    ) -> bool:
        """Apply the user's answer to ``policy``.

        Returns:
            True when the request existed and was applied; False for an
            unknown id — refused rather than silently accepted, because a
            stale prompt answering a question nobody asked is worse than an
            error.

        Raises:
            ValueError: for a lifetime outside the documented three. Guessing
                here would quietly widen a grant.
        """
        if lifetime not in _LIFETIMES:
            raise ValueError(f"unknown approval lifetime {lifetime!r}")

        if decision not in ("allow", "deny"):
            raise ValueError(f"unknown approval decision {decision!r}")
        pending = self._by_id.get(approval_id)
        if pending is None:
            return False
        if pending.capability in _NOT_GRANTABLE:
            return False
        validate_request(pending.origin, pending.capability)
        if lifetime not in allowed_lifetimes(pending.turn_id, pending.thread_id):
            raise ValueError("approval scope is missing")
        if decision == "allow" and decide(
            policy, url=pending.origin, capability=pending.capability,
            turn_id=pending.turn_id, thread_id=pending.thread_id,
        ).verdict == "deny":
            return False

        if lifetime == "always":
            self._write_origin(policy, pending, verdict=decision)
            self._by_id.pop(approval_id)
            return True

        # Session grant. `policy.grant` itself refuses the capabilities that
        # are not grantable, so this cannot widen anything by accident.
        policy.grant(
            origin=pending.origin,
            capability=pending.capability,  # type: ignore[arg-type]
            lifetime=lifetime,
            turn_id=pending.turn_id,
            thread_id=pending.thread_id,
            verdict=decision,
        )
        self._by_id.pop(approval_id)
        return True

    @staticmethod
    def _write_origin(policy: BrowserPolicy, pending: PendingApproval, *, verdict: str) -> None:
        """Set one capability at one origin, leaving the others alone."""
        existing = policy.origins.get(pending.origin) or OriginPolicy()
        fields = {
            name: getattr(existing, name) for name in OriginPolicy.__dataclass_fields__
        }
        # Defence in depth: `request()` already refuses these, but a forged or
        # replayed id must not be able to write one as configuration either.
        if pending.capability in _NOT_GRANTABLE and verdict == "allow":
            return
        if pending.capability in fields:
            fields[pending.capability] = verdict
        policy.origins[pending.origin] = OriginPolicy(**fields)
