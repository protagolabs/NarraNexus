"""
@file_name: factory.py
@author:
@date: 2026-08-10
@description: Composition root for AgentDataStore (blueprint P0).

Picks the transport the SAME way db_factory picks a db backend and broker_client
gates on BROKER_URL: keyed on ``NARRANEXUS_BACKEND_URL``.
  - unset  -> DirectStore  (local db access — the current behaviour everywhere
              until P2 flips cloud over; keeps `bash run.sh` / DMG unchanged and
              is a no-op no-behaviour-change import for existing cloud too)
  - set    -> HttpStore    (call the backend API; mcp holds no db creds)

The switch is a single env var so there is no scattered `if is_cloud` in tools
(rule #9/#20). ``identity_headers`` forwards the caller identity to the backend
on the Http path (populated from the live MCP request via
``current_identity_headers``); DirectStore ignores it.
"""
from __future__ import annotations

import os
from typing import Optional

from xyz_agent_context.module.data_access.store import (
    AgentDataStore,
    DirectStore,
    HttpStore,
)


def current_identity_headers() -> dict:
    """The caller-identity headers to forward to the backend on the Http path.

    Reuses the same header set the executor→mcp hop carries, read from the live
    MCP request context; empty when there is no ambient request (e.g. tests /
    the Direct path). Kept here so the store layer never imports request
    internals directly.
    """
    try:
        from xyz_agent_context.module._mcp_identity import _ambient_headers

        raw = _ambient_headers() or {}
        # forward only the NarraNexus identity headers + the borrowed bearer
        keep = {}
        for k, v in raw.items():
            lk = k.lower()
            if lk.startswith("x-narranexus-") or lk == "authorization":
                keep[k] = v
        return keep
    except Exception:  # noqa: BLE001 — no ambient context / import issue -> none
        return {}


def get_agent_data_store(identity_headers: Optional[dict] = None) -> AgentDataStore:
    backend_url = os.environ.get("NARRANEXUS_BACKEND_URL", "").strip()
    if backend_url:
        return HttpStore(
            backend_url,
            identity_headers=identity_headers
            if identity_headers is not None
            else current_identity_headers(),
        )
    return DirectStore()
