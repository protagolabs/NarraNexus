"""
@file_name: policy_store.py
@date: 2026-09-23
@description: Atomic owner-managed browser permissions without whole-document overwrites.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from urllib.parse import urlparse

from narranexus.platform.browser._browser_impl.policy import (
    BrowserPolicy, OriginPolicy, _match_origin, decide, origin_of,
)
from narranexus.platform.utils.timezone import utc_now

_CAPABILITIES = ("full_cdp_access",)


class PolicyValidationError(ValueError):
    """Invalid user input, distinct from corrupt persisted policy data."""


def managed_origin(value: str) -> str:
    """Reject paths/credentials rather than silently grant a wider origin."""
    origin = origin_of(value)
    if origin is None:
        raise PolicyValidationError("An HTTP or HTTPS origin is required")
    parsed = urlparse(value.strip())
    host = parsed.hostname or ""
    if (parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or any(char.isspace() for char in host)
            or "*" in host.removeprefix("*.")):
        raise PolicyValidationError("Use an origin without credentials, path, query or fragment")
    return origin


class PolicyStore:
    """Expose implemented permissions and change one rule with compare-and-swap."""

    TABLE = "instance_browser_policies"

    def __init__(self, db) -> None:
        self._db = db

    async def get_public(self, agent_id: str) -> dict:
        raw = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        policy = BrowserPolicy.from_dict(json.loads(raw["policy_json"])) if raw else BrowserPolicy()
        defaults = {capability: policy.default_verdict(capability) for capability in _CAPABILITIES}
        rows = []
        for origin, entry in sorted(policy.origins.items()):
            if origin_of(origin) is None or entry.full_cdp_access is None:
                continue
            rows.append({"origin": origin, **{
                capability: decide(policy, url=origin, capability=capability).verdict
                for capability in _CAPABILITIES
            }})
        return {"agent_id": agent_id, "defaults": defaults, "origins": rows}

    async def set_rule(self, agent_id: str, *, origin: str, capability: str, verdict: str) -> dict:
        origin = managed_origin(origin)
        if capability not in _CAPABILITIES or verdict not in ("allow", "deny"):
            raise PolicyValidationError("Unsupported browser capability or verdict")
        matcher = BrowserPolicy(origins={origin: OriginPolicy()})

        def matches(site: str) -> bool:
            return _match_origin(matcher, site)[0] is not None

        while True:
            raw = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
            if raw is None:
                now = utc_now().isoformat()
                try:
                    await self._db.insert(self.TABLE, {
                        "agent_id": agent_id, "policy_json": json.dumps(BrowserPolicy().to_dict()),
                        "created_at": now, "updated_at": now,
                    })
                except Exception:
                    if await self._db.get_one(self.TABLE, {"agent_id": agent_id}) is None:
                        raise
                continue
            document = json.loads(raw["policy_json"])
            policy = BrowserPolicy.from_dict(document)
            policy.origins[origin] = replace(policy.origins.get(origin, OriginPolicy()), **{capability: verdict})
            policy._grants = {item for item in policy._grants if item[1] != capability or not matches(item[0])}
            policy._denials = {item for item in policy._denials if item[1] != capability or not matches(item[0])}
            pending = await self._db.get("instance_browser_approvals", {"agent_id": agent_id, "capability": capability})
            policy._approval_receipts.update(row["approval_id"] for row in pending if matches(row["origin"]))
            document.update(policy.to_dict())
            written = await self._db.execute(
                "UPDATE instance_browser_policies SET policy_json = %s, updated_at = %s "
                "WHERE BINARY agent_id = BINARY %s AND BINARY policy_json = BINARY %s",
                (json.dumps(document), utc_now().isoformat(), agent_id, raw["policy_json"]), fetch=False,
            )
            if written:
                return await self.get_public(agent_id)
            await asyncio.sleep(0)
