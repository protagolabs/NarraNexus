"""
@file_name: multipart.py
@author:
@date: 2026-09-09
@description: A long bus message sent in ordered parts, and its reassembly.

Why parts at all. Nothing in the bus caps a message — `bus_messages.content`
is TEXT — but the SENDER's model does: a reply of ~4-5k characters is about
where a tool call's argument JSON runs into the output-token budget, and the
loop's only honest answer is "the tool was NOT executed, send it in smaller
pieces" (NexusPower `unparsed_call_result`). Without a contract for pieces,
"smaller pieces" means N separate bus messages, and the wake signal starts the
recipient's turn on piece 1 while piece 2 is still being generated: the
recipient answers a fragment, the sender's later pieces arrive as replies to
that answer, and a user reported exactly this on 8/31 — long replies "cut
off", the recipient's turn "empty", and "This turn ended without delivering a
reply" firing again and again.

The contract: `message_agent(text, part_index=i, part_count=n)`. Each part is
its own `bus_messages` row (so nothing is ever truncated or re-encoded), tied
to a group by `part_group` (= part 1's message_id, resolved at the write edge
by `LocalMessageBus._resolve_part_group`, which also refuses an out-of-order
part). On the recipient side the trigger calls `assemble` on a lane's batch:

* an INCOMPLETE group younger than `PART_ASSEMBLY_GRACE_SECONDS` is HELD — the
  lane returns without acking, and the last part's `wake_signal.bump` brings
  the poll loop back;
* a complete group collapses into ONE message: content is the parts joined
  with nothing in between (they are substrings, not paragraphs), attachments
  are the union, identity (message_id, event_id, root_run_id, turn source)
  is part 1's, `created_at` is the last part's (the ack cursor must pass every
  row), and `part_message_ids` lists every row so per-row bookkeeping
  (delivery receipts) still reaches each part;
* an incomplete group OLDER than the grace — or SUPERSEDED, i.e. the same
  sender has since opened a newer group in this channel, which the write edge
  guarantees the old one can never be continued past — is delivered as what
  arrived plus an explicit marker naming the missing parts. A sender whose
  turn died between parts must not block the recipient forever (iron rule
  #14: the platform is never the interruption source), and a silent gap would
  be the content loss iron rule #16 forbids.

`MAX_MESSAGE_PARTS` stays under the lane's pending-batch LIMIT (50) so a whole
group always fits one batch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from narranexus.platform.message_bus.schemas import BusMessage
from narranexus.platform.utils.timezone import coerce_utc

#: Upper bound on parts per message. Below the pending-batch LIMIT of 50 so a
#: complete group is always visible to one lane batch.
MAX_MESSAGE_PARTS = 40

#: How long an incomplete group is held for its remaining parts, measured from
#: the NEWEST part that arrived. Generous on purpose: the sender is a model
#: mid-turn and a thinking model can take minutes between tool calls; the
#: only thing this bounds is how long a sender that DIED between parts can
#: keep the recipient waiting.
PART_ASSEMBLY_GRACE_SECONDS = 600

_MISSING_MARKER = (
    "\n\n[platform: this message was sent in {count} parts; part(s) {missing} "
    "never arrived within {grace}s and the rest is delivered as-is]"
)


def _age_seconds(created_at, now: datetime) -> float:
    ts = coerce_utc(created_at)
    if ts is None:
        return float("inf")
    return (now - ts).total_seconds()


def _merge(parts: List[BusMessage], *, missing: List[int], grace: int) -> BusMessage:
    first, last = parts[0], parts[-1]
    content = "".join(p.content for p in parts)
    if missing:
        content += _MISSING_MARKER.format(
            count=first.part_count,
            missing=", ".join(str(i) for i in missing),
            grace=grace,
        )
    attachments: List[dict] = []
    for p in parts:
        attachments.extend(p.attachments or [])
    return first.model_copy(update={
        "content": content,
        "attachments": attachments or None,
        "created_at": last.created_at,
        "part_message_ids": [p.message_id for p in parts],
    })


def assemble(
    messages: List[BusMessage],
    *,
    now: Optional[datetime] = None,
    grace_seconds: int = PART_ASSEMBLY_GRACE_SECONDS,
) -> Tuple[List[BusMessage], bool]:
    """Collapse part rows in a lane batch into whole messages.

    Returns ``(deliverable, held)``. ``deliverable`` is what the caller may
    hand to a turn NOW, sorted by ``created_at`` (a merged message sits at its
    LAST part's time, so the newest deliverable row is always the ack
    high-water). ``held`` is True when some rows were withheld because a
    group is still incomplete and young.

    The one invariant (review I3/I6): the lane's ack cursor advances to the
    newest DELIVERED row, and no held row may lie below it. So everything at
    or after the oldest held row is withheld too — an unrelated message that
    arrived after part 1 waits with the group rather than being delivered now
    and delivered again when the group completes. Everything before the
    oldest held row is delivered as usual.
    """
    now = now or datetime.now(timezone.utc)
    groups: Dict[str, List[BusMessage]] = {}
    # The newest group each sender opened in this batch: an older incomplete
    # group of the same sender is superseded (the write edge only ever
    # continues the sender's LATEST part), so holding for it would wait for
    # parts that can no longer be written.
    latest_group_of: Dict[str, str] = {}
    for m in messages:
        if m.part_group:
            groups.setdefault(m.part_group, []).append(m)
            latest_group_of[m.from_agent] = m.part_group

    merged: Dict[str, BusMessage] = {}
    held_groups: set = set()
    for group_id, parts in groups.items():
        parts.sort(key=lambda p: int(p.part_index or 0))
        count = int(parts[0].part_count or 0)
        have = {int(p.part_index or 0) for p in parts}
        missing = [i for i in range(1, count + 1) if i not in have]
        if missing:
            superseded = latest_group_of.get(parts[0].from_agent) != group_id
            newest = min(_age_seconds(p.created_at, now) for p in parts)
            if not superseded and newest < grace_seconds:
                held_groups.add(group_id)
                continue
        merged[group_id] = _merge(parts, missing=missing, grace=grace_seconds)

    held_from: Optional[str] = None
    if held_groups:
        held_from = min(
            _ts(m.created_at) for m in messages if m.part_group in held_groups
        )

    out: List[BusMessage] = []
    emitted: set = set()
    for m in messages:
        if held_from is not None and _ts(m.created_at) >= held_from:
            continue
        if not m.part_group:
            out.append(m)
        elif m.part_group in merged and m.part_group not in emitted:
            emitted.add(m.part_group)
            whole = merged[m.part_group]
            if held_from is None or _ts(whole.created_at) < held_from:
                out.append(whole)
    out.sort(key=lambda m: _ts(m.created_at))
    return out, held_from is not None


def _ts(value) -> str:
    """Cursor-comparable timestamp text (the bus's own convention)."""
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


__all__ = [
    "MAX_MESSAGE_PARTS",
    "PART_ASSEMBLY_GRACE_SECONDS",
    "assemble",
]
