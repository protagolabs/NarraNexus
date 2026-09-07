"""
@file_name: binding_tables.py
@author: Bin Liang
@date: 2026-09-07
@description: Where an agent's channel bindings live, as SQL sources the agents directory can UNION: one parameterised view of ``channel_credentials`` per registered IM channel, plus the binding-only tables (no secret, so not a credential row).

Every value that reaches the SQL text is fixed by this module: the derived-table
alias is an INDEX (``src_0``), never the channel name, and the channel name
travels as a bound parameter. ``ChannelDescriptor.__post_init__`` already
restricts names to ``[a-z0-9_]`` — that validator is load-bearing for nothing
here any more, which is the point: a relaxed validator must not become an
injection on the first screen after login. Consumed by ``backend/routes/auth.py``
(agents directory); exercised on MySQL by
``tests/backend/test_auth_agents_directory_mysql.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TypedDict

CHANNEL_CREDENTIALS_TABLE = "channel_credentials"


class _BindingTableSpec(TypedDict):
    channel: str
    # Column that says the binding is switched on; None = presence is the
    # whole story (the table has no on/off switch).
    active_col: "str | None"


BINDING_ONLY_TABLES: dict[str, _BindingTableSpec] = {
    "instance_homeassistant_bindings": {"channel": "home_assistant", "active_col": None},
}


@dataclass(frozen=True)
class BindingSource:
    """One FROM-target a consumer may write ``SELECT agent_id, … FROM {source} WHERE …`` against, on both dialects."""

    channel: str
    source: str  # a table name, or an aliased sub-select with ``%s`` placeholders
    params: tuple  # the placeholders' values, in order — prepend them to the consumer's own
    active_col: Optional[str]  # None = presence means bound


def channel_binding_sources() -> list[BindingSource]:
    """Descriptor order (= display order) for the IM channels, then the binding-only tables."""
    from narranexus.platform.channel.credential_store import all_descriptors

    out = [
        BindingSource(
            channel=d.name,
            source=f"(SELECT agent_id, enabled FROM {CHANNEL_CREDENTIALS_TABLE} WHERE channel = %s) AS src_{i}",
            params=(d.name,),
            active_col="enabled",
        )
        for i, d in enumerate(all_descriptors())
    ]
    out.extend(BindingSource(spec["channel"], table, (), spec["active_col"]) for table, spec in BINDING_ONLY_TABLES.items())
    return out


def bound_channels_query(sources: list[BindingSource], agent_ids: list[str]) -> tuple[str, tuple]:
    """The UNION ALL over ``sources`` for ``agent_ids`` and its full parameter tuple.

    Each branch: ``SELECT %s AS channel_name, agent_id, <active> AS active FROM
    <source> WHERE agent_id IN (…)``; the channel name is a bound parameter too.
    """
    placeholders = ",".join(["%s"] * len(agent_ids))
    parts: list[str] = []
    params: list = []
    for src in sources:
        active = src.active_col if src.active_col else "1"
        parts.append(f"SELECT %s AS channel_name, agent_id, {active} AS active FROM {src.source} WHERE agent_id IN ({placeholders})")
        params.extend([src.channel, *src.params, *agent_ids])
    return " UNION ALL ".join(parts), tuple(params)


__all__ = ["BINDING_ONLY_TABLES", "BindingSource", "CHANNEL_CREDENTIALS_TABLE", "bound_channels_query", "channel_binding_sources"]
