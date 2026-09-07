"""
@file_name: channel_credential_tables.py
@author: Bin Liang
@date: 2026-09-04
@description: How IM channel credentials travel in an ``.nxbundle`` (opt-in): the generic ``channel_credentials`` rows, plus the legacy per-table shape of bundles exported before plugin-platform batch 4d.

Export writes ``agents/<id>/channel_credentials.json`` as
``{"channel_credentials": [row, …]}`` where a row is the generic record's
public view plus its DECRYPTED secrets (the encryption key is per install,
so ciphertext could not travel). Import lands every row INACTIVE, remaps
``agent_id`` and skips a binding whose external id is already bound here.
Legacy bundles carry ``{"<legacy table>": [bespoke rows]}``; they are
translated through ``channel/credential_legacy.py``.
"""
from __future__ import annotations

from typing import List, Dict, TypedDict, Any, Optional

CHANNEL_CREDENTIALS_KEY = "channel_credentials"


def legacy_rows_to_generic(table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate a legacy bundle's per-table rows into generic rows (empty for an unknown table)."""
    from narranexus.platform.channel.credential_legacy import LEGACY_BY_TABLE

    spec = LEGACY_BY_TABLE.get(table)
    if spec is None:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        values, enabled = spec.to_values(dict(row))
        external: Optional[str] = None
        for col in spec.identity_cols:
            if row.get(col):
                external = str(row[col])
                break
        out.append({"channel": spec.channel, "agent_id": row.get("agent_id", ""), "enabled": enabled, "external_id": external, **values})
    return out


def bundle_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every credential row in a bundle's channel_credentials.json, generic and legacy shapes alike."""
    rows: list[dict[str, Any]] = []
    for key, value in (payload or {}).items():
        if not isinstance(value, list):
            continue
        if key == CHANNEL_CREDENTIALS_KEY:
            rows.extend(r for r in value if isinstance(r, dict))
        else:
            rows.extend(legacy_rows_to_generic(key, [r for r in value if isinstance(r, dict)]))
    return rows


__all__ = ["CHANNEL_CREDENTIALS_KEY", "bundle_rows", "legacy_rows_to_generic"]


# ---------------------------------------------------------------------------
# Channel presence for user-facing projections (the agents directory / profile)
# ---------------------------------------------------------------------------
#
# Merged from dev (#383): the directory asks "which channels is this agent
# bound on, and is the binding switched on?". On the generic credential store
# (batch 4) every IM channel lives in ONE table keyed by ``channel``, so each
# channel's source is a filtered view of that table; binding-only tables (no
# secret, so not a credential row) are listed explicitly.
class _BindingTableSpec(TypedDict):
    channel: str
    # Column that says the binding is switched on; None = presence is the
    # whole story (the table has no on/off switch).
    active_col: "str | None"


BINDING_ONLY_TABLES: Dict[str, _BindingTableSpec] = {
    "instance_homeassistant_bindings": {"channel": "home_assistant", "active_col": None},
}


def channel_binding_tables() -> List[tuple]:
    """``(channel_key, source, active_col | None)`` for every source that binds an agent
    to a channel — one filtered view of ``channel_credentials`` per registered IM
    channel (descriptor order = display order), then the binding-only tables.
    ``source`` is a SQL FROM-target (a table name, or an aliased sub-select), so a
    consumer may write ``SELECT ... FROM {source} WHERE agent_id IN (...)`` on both
    dialects."""
    from narranexus.platform.channel.credential_store import all_descriptors

    out: List[tuple] = [
        (
            d.name,
            f"(SELECT agent_id, enabled FROM {CHANNEL_CREDENTIALS_KEY} WHERE channel = '{d.name}') AS src_{d.name}",
            "enabled",
        )
        for d in all_descriptors()
    ]
    out.extend((spec["channel"], table, spec["active_col"]) for table, spec in BINDING_ONLY_TABLES.items())
    return out

