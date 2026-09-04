"""
@file_name: credential_legacy.py
@author: Bin Liang
@date: 2026-09-04
@description: Copy the six retired per-channel credential tables into ``channel_credentials`` (migrations m0004/m0005) and convert legacy bundle rows.

Each retired table stored secrets base64-encoded in ``*_encoded`` /
``*_encrypted`` columns and its own active flag; ``LEGACY_TABLES`` maps a
table to its channel, active column and column→field translation. The copy
is idempotent (a binding already in the generic table is left alone — the
managers write there since batch 4d) and never drops the old tables (rule
#6). The same translation lets an ``.nxbundle`` exported before 4d import.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger

from xyz_agent_context.channel.credential_store import GenericCredentialStore


def _b64(value: Any) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode(str(value).encode()).decode()
    except Exception:  # noqa: BLE001 — a malformed legacy value is kept as-is
        return str(value)


def _json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


@dataclass(frozen=True)
class LegacyTable:
    table: str
    channel: str
    active_col: str
    # column -> (field, converter)
    columns: dict[str, tuple[str, Optional[Callable[[Any], Any]]]] = field(default_factory=dict)
    identity_cols: tuple[str, ...] = ()

    def to_values(self, row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        values: dict[str, Any] = {}
        for col, val in row.items():
            if col in ("id", "agent_id", self.active_col, "created_at", "updated_at"):
                continue
            target = self.columns.get(col)
            if target is None:
                values[col] = val
                continue
            name, conv = target
            values[name] = conv(val) if conv else val
        return values, bool(row.get(self.active_col, 1))


LEGACY_TABLES: tuple[LegacyTable, ...] = (
    LegacyTable("channel_telegram_credentials", "telegram", "enabled", {"bot_token_encoded": ("bot_token", _b64)}, ("bot_user_id",)),
    LegacyTable("channel_slack_credentials", "slack", "enabled", {"bot_token_encoded": ("bot_token", _b64), "app_token_encoded": ("app_token", _b64)}, ("team_id", "bot_user_id")),
    LegacyTable("channel_discord_credentials", "discord", "enabled", {"bot_token_encoded": ("bot_token", _b64)}, ("bot_user_id",)),
    LegacyTable("channel_wechat_credentials", "wechat", "enabled", {"bot_token_encoded": ("bot_token", _b64)}, ()),
    LegacyTable(
        "channel_narramessenger_credentials", "narramessenger", "enabled",
        {"bearer_token_encoded": ("bearer_token", _b64), "matrix_access_token_encoded": ("matrix_access_token", _b64)},
        ("matrix_user_id",),
    ),
    LegacyTable(
        "lark_credentials", "lark", "is_active",
        {"app_secret_encrypted": ("app_secret_encoded", None), "permission_state": ("permission_state", _json_dict)},
        ("app_id",),
    ),
)
LEGACY_BY_TABLE = {t.table: t for t in LEGACY_TABLES}


async def copy_legacy_tables(db: Any) -> dict[str, int]:
    """Copy every row of the retired tables that has no generic row yet. Returns rows copied per channel."""
    store = GenericCredentialStore(db)
    counts: dict[str, int] = {}
    for spec in LEGACY_TABLES:
        copied = 0
        try:
            rows = await db.get(spec.table, {})
        except Exception as exc:  # noqa: BLE001 — a table that never existed on this install
            logger.debug(f"[credential-legacy] {spec.table}: skipped ({exc})")
            continue
        for row in rows:
            agent_id = row.get("agent_id")
            if not agent_id:
                continue
            try:
                if await store.get(spec.channel, agent_id) is not None:
                    continue
                values, enabled = spec.to_values(dict(row))
                await store.upsert(spec.channel, agent_id, values, enabled=enabled)
                copied += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[credential-legacy] {spec.table}/{agent_id}: not copied: {exc}")
        counts[spec.channel] = copied
    return counts


__all__ = ["LEGACY_BY_TABLE", "LEGACY_TABLES", "LegacyTable", "copy_legacy_tables"]
