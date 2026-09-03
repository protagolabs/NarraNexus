"""
@file_name: channel_credential_tables.py
@author: NetMind.AI
@date: 2026-07-10
@description: Single source of truth for the IM channel credential tables that
             the bundle opt-in export/import feature carries.

Why this file exists
====================
Three call sites need the SAME per-table metadata about IM channel credentials
and must not drift:

- ``builder.py``   — which tables to read when ``include_channel_credentials``
                     is on.
- ``importer.py``  — which column to force to 0 on import (the anti-double-
                     connect invariant), and to exempt these tables from the
                     generic user-attribution rewrite (their owner columns are
                     IM-namespace ids, not NarraNexus user ids).
- ``preflight``    — which columns form the bot-identity uniqueness key, to
                     detect "this bot is already bound in the target env".

Design note — activation semantics
==================================
IM credentials always import as INACTIVE (``active_col`` forced to 0). The user
must explicitly activate the channel in the new environment, which is the moment
of claiming the single WebSocket slot the IM issues per app. This prevents a
migrated agent from silently double-connecting the same bot from both the source
and target environment.
"""

from typing import Dict, List, TypedDict


class _CredTableSpec(TypedDict):
    active_col: str          # column flipped to 0 on import (force inactive)
    identity_cols: List[str]  # bot-identity uniqueness key (clash detection)


# table_name -> spec. `agent_id` is the per-agent binding column on every table
# (registered separately in id_field_map for ID rewrite). `identity_cols` names
# the columns backed by a UNIQUE index that encodes the external bot identity;
# an empty list means the table has no bot-identity uniqueness constraint, so a
# same-bot clash cannot arise on import (agent_id is always freshly minted).
CHANNEL_CREDENTIAL_TABLES: Dict[str, _CredTableSpec] = {
    "lark_credentials": {
        "active_col": "is_active",
        # app_id is the Lark app = the real bot identity that owns the single WS
        # slot, matching the other channels' bot-identity keys. (NOT profile_name:
        # that is build_profile_name(agent_name, agent_id) — agent-derived and
        # preserved verbatim on import, so it never matches in the target env and
        # the clash check would be a silent no-op.)
        "identity_cols": ["app_id"],
    },
    "channel_slack_credentials": {
        "active_col": "enabled",
        "identity_cols": ["team_id", "bot_user_id"],
    },
    "channel_telegram_credentials": {
        "active_col": "enabled",
        "identity_cols": ["bot_user_id"],
    },
    "channel_discord_credentials": {
        "active_col": "enabled",
        "identity_cols": ["bot_user_id"],
    },
    "channel_wechat_credentials": {
        "active_col": "enabled",
        "identity_cols": [],
    },
    "channel_narramessenger_credentials": {
        "active_col": "enabled",
        "identity_cols": [],
    },
}


# ---------------------------------------------------------------------------
# Channel keys for user-facing projections (the agents directory / profile)
# ---------------------------------------------------------------------------
#
# ``CHANNEL_KEY_BY_TABLE`` maps each credential table above to the channel key
# the frontend brands it under. It lives here — not in a route — so adding an
# IM channel is ONE entry in this module: bundle export/import, preflight and
# the directory's "bound channels" column all follow. Keep the keys aligned
# with the frontend's channel brand map.
CHANNEL_KEY_BY_TABLE: Dict[str, str] = {
    "lark_credentials": "lark",
    "channel_slack_credentials": "slack",
    "channel_telegram_credentials": "telegram",
    "channel_discord_credentials": "discord",
    "channel_wechat_credentials": "wechat",
    "channel_narramessenger_credentials": "narramessenger",
}
# A registry-consistency contract, not a debug assertion: `python -O` strips
# `assert`, and this must fail at import in every build rather than surface as
# a KeyError inside the first request that touches it.
if set(CHANNEL_KEY_BY_TABLE) != set(CHANNEL_CREDENTIAL_TABLES):
    raise RuntimeError(
        "channel_credential_tables: CHANNEL_KEY_BY_TABLE and "
        "CHANNEL_CREDENTIAL_TABLES must list the same tables"
    )


class _BindingTableSpec(TypedDict):
    channel: str
    # Column that says the binding is switched on; None = presence is the
    # whole story (the table has no on/off switch).
    active_col: "str | None"


# Per-agent channel BINDING tables that are NOT bundle credential objects (no
# secret to export, so they are not in CHANNEL_CREDENTIAL_TABLES) but still
# count as "this agent is reachable on channel X" for the directory.
BINDING_ONLY_TABLES: Dict[str, _BindingTableSpec] = {
    "instance_homeassistant_bindings": {"channel": "home_assistant", "active_col": None},
}


def channel_binding_tables() -> List[tuple]:
    """``(channel_key, table, active_col | None)`` for every table that binds
    an agent to a channel — credential-backed and binding-only alike. The
    directory query is built from this so it cannot drift from the bundle
    registry above.

    The RETURN ORDER is also the display order of the directory's channel
    icons (credential tables in registry order, then binding-only tables).
    Re-ordering ``CHANNEL_CREDENTIAL_TABLES`` therefore re-orders the UI."""
    out: List[tuple] = [
        (CHANNEL_KEY_BY_TABLE[table], table, spec["active_col"])
        for table, spec in CHANNEL_CREDENTIAL_TABLES.items()
    ]
    out.extend(
        (spec["channel"], table, spec["active_col"])
        for table, spec in BINDING_ONLY_TABLES.items()
    )
    return out
