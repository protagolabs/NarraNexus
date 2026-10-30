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
sub-second stamps are trusted: SQLite's datetime('now') has whole seconds (the
backend returns it as a datetime with microsecond 0), so a second write in the
same second would leave updated_at unchanged; such rows are re-read on every
call (local single-user mode).

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

#: Most agent ids one call accepts. The sidebar's chunk size must not exceed it:
#: frontend/src/hooks/useAutoRefresh.ts CHAT_LATEST_CHUNK — change both together
#: (a larger client chunk would be refused with 422 on every tick).
CHAT_LATEST_MAX_AGENTS = 200

#: Generous upper bound on one agent id's length (ids are ~15 characters).
CHAT_LATEST_ID_MAX_LENGTH = 128

#: Full memory rows fetched per query on a cache miss.
_MEMORY_READ_CHUNK = 20

#: (instance_id, updated_at) stamps fetched per query. The instance count is not
#: bounded by the agent count (ChatModule instances are per narrative and are
#: never archived), so the IN list is chunked rather than sent whole.
_STAMP_READ_CHUNK = 500

Latest = Optional[Tuple[datetime, str]]

#: instance_id -> (memory row updated_at, newest conversation row or None).
#: Bounded; least recently used evicted first.
_LATEST_CACHE: "OrderedDict[str, Tuple[Any, Latest]]" = OrderedDict()
_LATEST_CACHE_MAX = 20_000


def _fold(best: Latest, msg: Dict[str, Any]) -> Latest:
    """Fold one message into the newest conversation row seen so far. Rows
    project_message drops or marks 'activity', and rows without a timestamp,
    do not count; ties keep the later message, like the tab's stable sort."""
    row = project_message(msg, peer_scoped=False)
    if row is None or row["message_type"] == "activity" or not row["timestamp"]:
        return best
    parsed = parse_timestamp(row["timestamp"])
    if best is None or parsed >= best[0]:
        return (parsed, str(row["timestamp"]))
    return best


def stamp_is_precise(updated_at: Any) -> bool:
    """Whether an updated_at value can tell two writes in the same second apart.

    MySQL's DATETIME(6) comes back as a datetime with microseconds; SQLite's
    datetime('now') has whole seconds (a datetime with microsecond 0, or a
    string without a fraction from a backend that does not parse it).
    Whole-second stamps are never trusted (the row is re-read every call). A
    MySQL write that lands exactly on .000000 is merely re-read too."""
    if isinstance(updated_at, datetime):
        return updated_at.microsecond != 0
    return isinstance(updated_at, str) and "." in updated_at


def _cache_put(instance_id: str, updated_at: Any, latest: Latest) -> None:
    _LATEST_CACHE[instance_id] = (updated_at, latest)
    _LATEST_CACHE.move_to_end(instance_id)
    while len(_LATEST_CACHE) > _LATEST_CACHE_MAX:
        _LATEST_CACHE.popitem(last=False)


#: (instance_id, updated_at) last warned about, so a malformed row re-read on
#: every call (whole-second stamps) is reported once per version, not per tick.
_WARNED: "OrderedDict[str, Any]" = OrderedDict()


def _parse_row(instance_id: str, memory: Any, updated_at: Any) -> Latest:
    """One instance's newest row; a malformed blob costs only this instance.

    Fails the same way the conversation tab does: its loop appends rows in order
    and stops at the first message it cannot read, keeping the rows before it —
    so the fold here stops there too and keeps what it had."""
    best: Latest = None
    try:
        data = parse_json_field(memory, {})
        messages = data.get("messages", []) if isinstance(data, dict) else []
        for msg in messages:
            best = _fold(best, msg)
        return best
    except Exception as e:  # noqa: BLE001 — one bad row must not blank the whole list
        if _WARNED.get(instance_id, object()) != updated_at:
            _WARNED[instance_id] = updated_at
            _WARNED.move_to_end(instance_id)
            while len(_WARNED) > _LATEST_CACHE_MAX:
                _WARNED.popitem(last=False)
            logger.warning(f"chat-latest: unreadable chat memory for instance {instance_id}: {e}")
        return best


async def chat_latest_for_user(db, user_id: str, agent_ids: List[str]) -> Dict[str, str]:
    """agent_id -> raw timestamp of the newest conversation-tab row, over the
    caller's OWN ChatModule instances of those agents (agents without one are
    absent)."""
    rows = await InstanceRepository(db).list_ids_by_agents_and_user(agent_ids, user_id, "ChatModule")
    rows = [r for r in rows if r.get("status") not in ("cancelled", "archived")]
    if not rows:
        return {}

    instance_ids = [r["instance_id"] for r in rows]
    stamps: List[Optional[Dict[str, Any]]] = []
    for i in range(0, len(instance_ids), _STAMP_READ_CHUNK):
        # get_by_ids keeps the order of its ids and pads misses with None, so
        # each chunk lines up with its own slice of instance_ids.
        stamps.extend(await db.get_by_ids(
            CHAT_MEMORY_TABLE, "instance_id", instance_ids[i:i + _STAMP_READ_CHUNK],
            fields=["instance_id", "updated_at"],
        ))

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
            latest = _parse_row(iid, mem.get("memory"), mem.get("updated_at"))
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
