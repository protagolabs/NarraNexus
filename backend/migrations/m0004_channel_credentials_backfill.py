"""
@file_name: m0004_channel_credentials_backfill.py
@author: Bin Liang
@date: 2026-09-04
@description: One-shot backfill of the six bespoke channel credential tables into ``channel_credentials`` (plugin platform batch 4b, dual-write phase).

Idempotent: ``sync_from_manager`` upserts by (channel, agent_id). Only active
rows are copied; an inactive binding reaches the generic table on its next
write (the manager mirror) — 4d re-runs a full copy before switching reads.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from . import Migration

if TYPE_CHECKING:
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient


async def _apply(db: "AsyncDatabaseClient") -> Dict:
    from xyz_agent_context.channel.credential_mirror import backfill

    counts = await backfill(db)
    return {"channels": counts, "rows": sum(counts.values())}


MIGRATION = Migration(
    id="0004_channel_credentials_backfill",
    description="copy active bespoke channel bindings into channel_credentials (dual-write phase)",
    apply=_apply,
)
