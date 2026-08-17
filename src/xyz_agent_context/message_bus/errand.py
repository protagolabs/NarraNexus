"""
@file_name: errand.py
@author:
@date: 2026-08-14
@description: The message-level half of the work board — a hand-off records
              itself.

The board (#259) could already notice a stalled task and chase it. What it
could not do was FIND OUT about one: its only entrance was `work_add_item`, a
tool the Leader has to remember to call. That put the entire guard on model
obedience, which is the dependency iron rule #15 keeps off correctness-critical
paths — and the founding case walks straight through it. During the Dunhuang
chain nobody called the tool, so the board was empty and patrol had nothing to
sweep.

This module is the entrance the owner specified on 2026-08-07: an errand is a
MESSAGE-level fact recorded automatically (A @ B opens it, B's reply closes
it), layered under the TASK-level work item that tools maintain explicitly.
Both live in `team_work_items`, told apart by `origin`.

Why the two halves are one module
=================================
Opening without closing is strictly worse than nothing. Every "@Bruno 你怎么看"
would land on a board that every member reads each turn, and patrol would chase
it until someone cleaned up by hand. The value of the entrance depends on the
exit existing, so they are written, changed and tested together.

The rule that carries the incident
==================================
**A promise does not close an errand.** "收到，开始处理……完成后交付 @A4" was
the entire final_output of the run that killed the pipeline. Reading it as
delivery would make the board agree with the runtime that the work was done,
which is precisely the blindness this exists to remove. So `is_promise_only`
biases towards leaving the errand open: a false "promise" costs one patrol
line, a false "delivery" costs the whole mechanism on the one message it was
built for.

The classifier is a heuristic and is allowed to be, because nothing
irreversible hangs off it: staying open means patrol asks once (capped by
`may_patrol_speak`), never that a run is stopped (iron rule #14). The prompt
discipline in `_build_team_prompt` reduces how often it is consulted at all;
it does not replace it (iron rule #15 — the prompt is not the guard).
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, List, Optional, Sequence

from loguru import logger

from xyz_agent_context.utils.timezone import utc_now

from xyz_agent_context.repository.team_work_repository import TeamWorkItemRepository
from xyz_agent_context.schema.team_work_schema import WorkItemOrigin, WorkItemStatus

#: An errand title is read by every member every turn, so it is short by
#: contract rather than by accident.
TITLE_MAX_CHARS = 80

#: How many hand-offs ONE message may open.
#:
#: The input is caller-controlled and previously unbounded: `targets` comes
#: straight from the @mentions in a model-written reply, and every row it
#: creates is rendered into every member's prompt on every turn, forever, until
#: something closes it. `PATROL_SPEECH_MAX` is this repository's own precedent
#: for capping a platform-generated volume.
#:
#: The number is also a latency budget, not only a board-length one. Since the
#: team reply moved inside the turn (#291/#302) this book-keeping runs while the
#: runtime waits on the delivery callback, at two indexed queries per target —
#: so an uncapped "@ twelve people" would sit in the agent's turn.
#:
#: A message addressing more people than this is a broadcast, and a broadcast is
#: `@everyone`, which opens nothing at all.
MAX_HANDOFFS_PER_MESSAGE = 5

#: How long an unfinished AUTO errand stays on the board.
#:
#: Auto errands have exactly two exits: the assignee delivers, or the lead
#: closes the item by hand — and the second one is the model obedience this
#: whole layer exists to stop depending on. So an errand that will never be
#: delivered is otherwise PERMANENT, and it does not merely sit there: `stalled`
#: counts as ACTIVE, so one stuck row keeps `teams_with_active_work()` returning
#: this team forever, pins patrol to its 180s stalled cadence forever, and burns
#: the speech budget every 30 minutes forever. `patrol.py` documents "empty
#: board = zero runs" as this feature's cost guarantee; without an expiry, one
#: rhetorical "@Bruno 你怎么看" retires that guarantee for the whole team.
#:
#: 24h rather than something tighter because the ceiling has to clear a
#: legitimately long hand-off — iron rule #14 protects runs that last tens of
#: hours, and expiring an errand out from under one that is still working would
#: make the board lie in the other direction.
ERRAND_TTL_HOURS = 24

#: Addressing the room is not handing work to a person: nobody is late on
#: `@everyone`, and opening one item per member would flood the board.
BROADCAST_MENTION = "@everyone"

# ---------------------------------------------------------------------------
# Promise vocabulary
#
# Deliberately small and deliberately explicit. The temptation is to reach for
# an LLM classifier here; that would put a correctness-critical decision back
# on model obedience (iron rule #15) AND add a model call to the delivery path
# of every team message.
#
# Two shapes, because the incident contains both:
#   * a bare acknowledgement ("收到", "on it") — nothing was delivered;
#   * an acknowledgement plus a FUTURE-tense delivery ("完成后交付 @A4") —
#     nothing was delivered and something was promised.
# ---------------------------------------------------------------------------
_PROMISE_PATTERNS: Sequence[re.Pattern] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # Future delivery, Chinese
        r"完成后", r"完成时", r"结束后", r"稍后", r"待会", r"一会儿?[再会]",
        r"之后(再)?(交付|汇报|同步|反馈|发)", r"回头(再)?(汇报|同步|发|说)",
        r"(随后|届时).{0,6}(汇报|同步|交付|反馈)",
        # Future delivery, English
        r"\bget back to (you|u)\b", r"\breport back\b", r"\bwill (let you know|update you|share)\b",
        r"\bonce (i'?m |it'?s )?(done|finished|ready)\b",
        r"\bwhen (i'?m |it'?s )?(done|finished|ready)\b",
        r"\b(i'?ll|i will) (send|share|post|deliver|have)\b",
        r"\blet me (look into|check|dig into|work on)\b",
    )
)

#: One acknowledgement token — a phrase that moves nothing forward on its own.
_ACK_TOKEN = (
    r"(?:收到|好的?|明白|了解|知道了|遵命|没问题|马上(?:处理|开始)?|开始处理|"
    r"我?(?:来|去)?(?:处理|办|做|跟进)一?下?|"
    r"ok(?:ay)?|got it|sure|will do|on it|roger|understood|ack(?:nowledged)?|"
    r"i'?ll (?:handle|take) (?:it|this))"
)

#: A whole message that is nothing BUT acknowledgement tokens. Anchored, so
#: "收到，报告在 notes.md" is not an ack — it carries a deliverable after the
#: acknowledgement. Repeated because people stack them ("OK, on it").
_BARE_ACK_RE = re.compile(
    rf"^[\s，,。.!！~、]*{_ACK_TOKEN}"
    rf"(?:[\s，,。.!！~、]+{_ACK_TOKEN})*"
    rf"[\s，,。.!！~、]*$",
    re.IGNORECASE,
)


def is_promise_only(text: str) -> bool:
    """True when ``text`` announces work instead of delivering it.

    Empty text counts as a promise (nothing was delivered), which keeps the
    errand open — the safe direction.
    """
    body = (text or "").strip()
    if not body:
        return True
    if _BARE_ACK_RE.match(body):
        return True
    return any(p.search(body) for p in _PROMISE_PATTERNS)


def _title_from(text: str) -> str:
    """The ask, without the markup the board would otherwise pay for.

    First non-empty line, `@name` tokens stripped, truncated. A title is a
    label for a row a model reads every turn — the message itself stays
    reachable through ``source_message_id``.
    """
    cleaned = re.sub(r"@\w+", " ", text or "")
    for line in cleaned.splitlines():
        line = " ".join(line.split())
        if line:
            return line[:TITLE_MAX_CHARS]
    return "(untitled hand-off)"


async def record_handoffs(
    db: Any,
    *,
    team_id: str,
    channel_id: str,
    from_agent: str,
    mentions: Optional[Sequence[str]],
    text: str,
    message_id: str,
    root_run_id: str = "",
) -> List[str]:
    """Open one errand per person this post hands work to. Returns new item ids.

    Called after the post has LANDED, never before: an errand for a message the
    room never saw would have the board and the transcript disagreeing, and the
    board is the thing patrol trusts.

    Best-effort by contract — the caller has already delivered the reply, and a
    board write must never be able to fail a hop that succeeded.
    """
    targets = [
        m for m in (mentions or [])
        if m and m != BROADCAST_MENTION and m != from_agent
    ]
    if not targets or not team_id or not channel_id:
        return []
    if len(targets) > MAX_HANDOFFS_PER_MESSAGE:
        # Announced, never silent: a cap that trims without saying so reads
        # downstream as "those hand-offs were never made" (iron rule #16's
        # rule for truncation, applied to the board instead of a transcript).
        dropped = targets[MAX_HANDOFFS_PER_MESSAGE:]
        logger.warning(
            f"[errand] {len(targets)} hand-offs in one message from "
            f"{from_agent} in {channel_id}; opening the first "
            f"{MAX_HANDOFFS_PER_MESSAGE}, not opening: {', '.join(dropped)}"
        )
        targets = targets[:MAX_HANDOFFS_PER_MESSAGE]

    repo = TeamWorkItemRepository(db)
    title = _title_from(text)
    opened: List[str] = []
    for assignee in targets:
        try:
            # The poll loop can re-deliver and a retried post keeps its id, so
            # the message is the dedup key — not the (agent, title) pair, which
            # would also swallow a genuine second hand-off of the same work.
            if await repo.has_errand_for(message_id, assignee):
                continue
            item = await repo.create_item(
                team_id=team_id,
                channel_id=channel_id,
                title=title,
                created_by=from_agent,
                assignee_id=assignee,
                source_message_id=message_id,
                root_run_id=root_run_id or None,
                origin=WorkItemOrigin.AUTO,
            )
        except Exception as e:  # noqa: BLE001 — see docstring
            logger.warning(
                f"[errand] open failed team={team_id} to={assignee}: "
                f"{type(e).__name__}: {e}"
            )
            continue
        opened.append(item.item_id)
        # The closure-rate metric reads these lines (scripts/diag_collector).
        logger.info(
            f"[work-item] action=open item={item.item_id} team={team_id} "
            f"channel={channel_id} assignee={assignee} from={from_agent} "
            f"origin={WorkItemOrigin.AUTO}"
        )
    return opened


async def close_delivered_errands(
    db: Any,
    *,
    team_id: str,
    channel_id: str,
    agent_id: str,
    text: str,
) -> List[str]:
    """Close this agent's open errands in this room, unless the post is a
    promise. Returns the ids closed.

    Scoped to one channel because an agent belongs to several teams: speaking
    in room X must not settle what it owes in room Y.

    Only ``origin=auto`` items are touched. A tool-made item is a TASK and one
    task routinely spans several errands (owner decision 2026-08-07) — auto
    closing it on the assignee's first message would collapse the two layers
    that decision separated.
    """
    if not team_id or not channel_id or not agent_id:
        return []
    if is_promise_only(text):
        # The Dunhuang case. Nothing to do — and that is the whole point.
        return []

    repo = TeamWorkItemRepository(db)
    try:
        open_items = await repo.list_open_errands(channel_id, agent_id)
    except Exception as e:  # noqa: BLE001 — never fail a delivered hop
        logger.warning(
            f"[errand] close lookup failed channel={channel_id} "
            f"agent={agent_id}: {type(e).__name__}: {e}"
        )
        return []

    # ONE delivery settles ONE errand — the oldest.
    #
    # Closing everything this agent owed was the first shape of this function
    # and it was wrong in exactly the way `is_promise_only` is written to avoid:
    # in a six-stage pipeline an agent routinely owes several things at once, so
    # delivering one of them would mark all of them done, and the rest would
    # vanish from the board with nobody chasing them and the panel showing
    # everything healthy. It also inflated the closure rate — one post, three
    # `close` lines — and that rate is the evidence PR #230 wants before anyone
    # decides a stronger fallback is unnecessary.
    #
    # The OLDEST rather than the newest: `list_open_errands` orders ascending,
    # and the one that has been open longest is both the likeliest to be what a
    # delivery refers to and the one closest to being chased.
    closed: List[str] = []
    for item in open_items[:1]:
        try:
            await repo.set_status(item.item_id, WorkItemStatus.DONE)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[errand] close failed item={item.item_id}: {e}")
            continue
        closed.append(item.item_id)
        logger.info(
            f"[work-item] action=close item={item.item_id} team={team_id} "
            f"channel={channel_id} assignee={agent_id} "
            f"origin={WorkItemOrigin.AUTO}"
        )
    return closed


async def expire_stale_errands(db: Any, team_id: str) -> List[str]:
    """Retire AUTO errands older than the TTL. Returns the ids expired.

    The recycler that makes automatic opening survivable. Without it an errand
    nobody will ever deliver is permanent, and permanence here is not passive:
    see ``ERRAND_TTL_HOURS`` for why one stuck row costs this team its patrol
    cadence and its speech budget indefinitely.

    Retired as ``cancelled``, never ``done``. ``done`` is what the closure-rate
    report counts as a delivery, so expiring into it would quietly restate
    "nobody ever got to this" as "it was delivered" — and that report is the
    whole evidentiary value of the metric work in this change.

    ``tool`` items are untouched: a Leader's task is explicit, and making one
    disappear on a timer is a different class of accident.

    Best-effort, like the rest of this module — it runs on the patrol path, and
    a failed sweep must not cost the sweep that follows it.
    """
    if not team_id:
        return []
    repo = TeamWorkItemRepository(db)
    try:
        # Reads the board through `list_active` and ages the rows in PYTHON,
        # rather than adding a `created_at < %s` predicate to a new statement.
        #
        # Two reasons, and the first one is a bug this took: `created_at` holds
        # two different textual shapes on SQLite — rows written by the schema
        # default land as `2026-08-17 02:52:40` while anything written from
        # Python lands as `2026-08-17T02:52:40+00:00`. A string comparison
        # across the two is wrong at the separator ('T' > ' '), so a SQL-side
        # cutoff silently matches nothing, and SQLite is the desktop build's
        # production backend. `patrol_due_at` already ages timestamps in Python
        # via `parse_db_utc` for the same reason.
        #
        # The second: it adds no raw SQL. `list_active` is one of the four
        # statements the real-MySQL suite already covers.
        candidates = await repo.list_active(team_id)
    except Exception as e:  # noqa: BLE001 — see docstring
        logger.warning(f"[errand] stale sweep failed team={team_id}: {e}")
        return []

    from xyz_agent_context.agent_runtime.run_recorder import parse_db_utc

    cutoff = utc_now() - timedelta(hours=ERRAND_TTL_HOURS)
    stale = []
    for item in candidates:
        if item.origin != WorkItemOrigin.AUTO:
            continue
        created = parse_db_utc(item.created_at)
        # An unparseable timestamp is not evidence of age. Skipping keeps the
        # row on the board, which is the same direction every other judgement
        # in this module leans.
        if created is not None and created < cutoff:
            stale.append(item)

    expired: List[str] = []
    for item in stale:
        try:
            await repo.set_status(item.item_id, WorkItemStatus.CANCELLED)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[errand] expire failed item={item.item_id}: {e}")
            continue
        expired.append(item.item_id)
        logger.info(
            f"[work-item] action=expire item={item.item_id} team={team_id} "
            f"channel={item.channel_id} assignee={item.assignee_id} "
            f"origin={WorkItemOrigin.AUTO}"
        )
    return expired
