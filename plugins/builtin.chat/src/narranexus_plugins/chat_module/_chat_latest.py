"""
@file_name: _chat_latest.py
@author: Bin Liang
@date: 2026-10-30
@description: Newest conversation-tab row per agent for one user, for a whole agent list — the body of GET /api/agents/chat-latest.

The sidebar's background "agent replied to you" signal used to call
simple-chat-history(include='chat') once per agent every 15s. This computes the
same answer for up to CHAT_LATEST_MAX_AGENTS agents in one call: for each agent,
the timestamp of the newest row the caller's conversation tab would show, using
the tab's own projection (_chat_projection.project_message), so the two cannot
drift.

Cost: one indexed instance listing, one projected (instance_id, updated_at) read
of the memory rows, and a full memory read + parse only for rows whose
updated_at changed since this process last looked (in chunks — memory blobs
grow without bound). The cache is a pure cache: an entry is used only while the
row's updated_at is unchanged, so every process is correct on its own. Only
sub-second stamps are trusted: SQLite's datetime('now') has whole seconds, so a
second write in the same second would leave updated_at unchanged; such rows are
re-read on every call (local single-user mode).

Writers this relies on: EventMemoryRepository.add_instance_json_format_memory
(upsert bumps updated_at to CURRENT_TIMESTAMP(6)); the bundle importer inserts
new rows (column default). A new writer that UPDATEs memory without bumping
updated_at would leave this cache stale.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from narranexus.platform.repository import InstanceRepository
from narranexus_plugins.chat_module._chat_projection import (
    CHAT_MEMORY_TABLE,
    parse_json_field,
    parse_timestamp,
    project_message,
)

#: Most agent ids one call accepts (the sidebar sends its list in chunks of this).
CHAT_LATEST_MAX_AGENTS = 200

#: Full memory rows fetched per query on a cache miss.
_MEMORY_READ_CHUNK = 20

Latest = Optional[Tuple[datetime, str]]

#: instance_id -> (memory row updated_at, newest conversation row or None).
#: Bounded; least recently used evicted first.
_LATEST_CACHE: "OrderedDict[str, Tuple[Any, Latest]]" = OrderedDict()
_LATEST_CACHE_MAX = 20_000


def latest_conversation_row(messages: List[Dict[str, Any]]) -> Latest:
    """(parsed, raw) timestamp of the newest row the conversation tab shows from
    one owner-scoped instance, or None. Rows project_message drops or marks
    'activity' do not count; ties keep the later message, like the tab's stable
    sort."""
    best: Latest = None
    for msg in messages:
        row = project_message(msg, peer_scoped=False)
        if row is None or row["message_type"] == "activity" or not row["timestamp"]:
            continue
        parsed = parse_timestamp(row["timestamp"])
        if best is None or parsed >= best[0]:
            best = (parsed, str(row["timestamp"]))
    return best


def stamp_is_precise(updated_at: Any) -> bool:
    """Whether an updated_at value can tell two writes in the same second apart.

    MySQL's DATETIME(6) comes back as a datetime with microseconds; SQLite's
    datetime('now') as a whole-second string. Whole-second stamps are never
    trusted (the row is re-read every call). A MySQL write that lands exactly on
    .000000 is merely re-read too."""
    if isinstance(updated_at, datetime):
        return updated_at.microsecond != 0
    return isinstance(updated_at, str) and "." in updated_at


def _cache_put(instance_id: str, updated_at: Any, latest: Latest) -> None:
    _LATEST_CACHE[instance_id] = (updated_at, latest)
    _LATEST_CACHE.move_to_end(instance_id)
    while len(_LATEST_CACHE) > _LATEST_CACHE_MAX:
        _LATEST_CACHE.popitem(last=False)


def _parse_row(instance_id: str, memory: Any) -> Latest:
    """One instance's newest row; a malformed blob costs only this instance."""
    try:
        data = parse_json_field(memory, {})
        messages = data.get("messages", []) if isinstance(data, dict) else []
        return latest_conversation_row([m for m in messages if isinstance(m, dict)])
    except Exception as e:  # noqa: BLE001 — one bad row must not blank the whole list
        logger.warning(f"chat-latest: unreadable chat memory for instance {instance_id}: {e}")
        return None


async def chat_latest_for_user(db, user_id: str, agent_ids: List[str]) -> Dict[str, str]:
    """agent_id -> raw timestamp of the newest conversation-tab row, over the
    caller's OWN ChatModule instances of those agents (agents without one are
    absent)."""
    rows = await InstanceRepository(db).list_ids_by_agents_and_user(agent_ids, user_id, "ChatModule")
    rows = [r for r in rows if r.get("status") not in ("cancelled", "archived")]
    if not rows:
        return {}

    instance_ids = [r["instance_id"] for r in rows]
    stamps = await db.get_by_ids(CHAT_MEMORY_TABLE, "instance_id", instance_ids, fields=["instance_id", "updated_at"])

    # Results for THIS call, so a concurrent call evicting cache entries during
    # the awaits below cannot drop an agent from this response.
    results: Dict[str, Latest] = {}
    changed: List[str] = []
    for iid, stamp in zip(instance_ids, stamps):
        if stamp is None:
            continue
        cached = _LATEST_CACHE.get(iid)
        if cached is not None and stamp_is_precise(stamp.get("updated_at")) and cached[0] == stamp.get("updated_at"):
            _LATEST_CACHE.move_to_end(iid)
            results[iid] = cached[1]
        else:
            changed.append(iid)

    for i in range(0, len(changed), _MEMORY_READ_CHUNK):
        chunk = changed[i:i + _MEMORY_READ_CHUNK]
        memory_rows = await db.get_by_ids(CHAT_MEMORY_TABLE, "instance_id", chunk, fields=["instance_id", "memory", "updated_at"])
        for iid, mem in zip(chunk, memory_rows):
            if mem is None:
                continue
            latest = _parse_row(iid, mem.get("memory"))
            _cache_put(iid, mem.get("updated_at"), latest)
            results[iid] = latest

    best: Dict[str, Tuple[datetime, str]] = {}
    for row in rows:
        latest = results.get(row["instance_id"])
        if latest is None:
            continue
        current = best.get(row["agent_id"])
        if current is None or latest[0] >= current[0]:
            best[row["agent_id"]] = latest
    return {aid: v[1] for aid, v in best.items()}
