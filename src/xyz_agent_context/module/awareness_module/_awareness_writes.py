"""
@file_name: _awareness_writes.py
@author:
@date: 2026-08-10
@description: The shared, dialect-safe implementation of the update_agent_profile
tool — the single source of truth behind the AgentDataStore seam.

update_agent_profile is a rename TRANSACTION (P1 section 02 ①, prod
evt_1f9c6680): writing agents.agent_name alone left the agent's free-text
long-term memory still asserting the old identity, and the old name may now
belong to a different agent of the same owner. So the write does four things —
name and/or description, a dated identity-correction appended into the Awareness
profile, an honest (never blocking) same-owner name-clash note, and an immediate
peer-directory refresh — and any one missing reinstates the original bug.

Hoisted here (out of awareness_module.py) so DirectStore (local) and the backend
twin route (cloud) both call THIS one function: they stay byte-identical, and the
rename-transaction logic cannot drift between the in-process and Http paths. The
identity-note string helpers and the two DB helpers live here too because they
are used only by this writer; keeping them together also breaks the import cycle
(awareness_module.py delegates the tool to the seam, so it must not be imported
back by the shared writer).

Dialect-safe (rule #6): AgentRepository / InstanceRepository /
InstanceAwarenessRepository and db.get only — no raw SQL. The MATCHED-vs-CHANGED
rowcount trap (update_agent returns cursor.rowcount = matched on SQLite, changed
on MySQL) is defused twice: BEFORE the write by the value-equality
short-circuits on both name and description, so an unchanged re-save returns
"No changes needed" identically on both dialects; and AFTER it by verifying
against the re-read row instead of the rowcount, so a write that DID land is
never reported as "did not apply" (2026-08-17 — until then only the first half
existed here, and the user-facing HTTP twin had neither).

Both halves compare with `agent_field_matches` from schema.entity_schema, which
is also what backend/routes/auth.py uses: the two writers of the agents row
previously disagreed about whether surrounding whitespace made a value
different.
"""
from __future__ import annotations

from typing import List, Optional

from loguru import logger

from xyz_agent_context.repository import (
    AgentRepository,
    InstanceAwarenessRepository,
    InstanceRepository,
)
from xyz_agent_context.schema.entity_schema import (
    AGENT_TEXT_MAX_LENGTH,
    agent_field_matches,
    normalize_agent_text,
)

# Where a rename records itself inside the Awareness profile. The profile is
# injected verbatim into the system prompt every turn, so this is the one place
# a correction is guaranteed to be read. (See module docstring for the incident.)
IDENTITY_CHANGE_SECTION = "## Identity Changes (platform record)"

# Keep the section bounded: renames are rare, but an unbounded log would eat
# the context window it lives in. Newest entries win.
MAX_IDENTITY_CHANGE_ENTRIES = 5


def build_identity_change_note(
    old_name: str, new_name: str, when: Optional[str] = None
) -> str:
    """One line recording a rename, written for the agent to read about itself.

    States both names and explicitly RETIRES the old one: the failure mode is
    memory that keeps asserting the previous identity, and "you are now X" does
    not contradict "I am Y" as far as a model is concerned — especially when
    the old name may now belong to a different agent of the same owner.
    """
    if when is None:
        from datetime import datetime, timezone
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"- {when}: renamed by your creator from 「{old_name}」 to 「{new_name}」. "
        f"You are 「{new_name}」. 「{old_name}」 is no longer your name — if it "
        f"appears in your memories or past conversations, that is history, and "
        f"it may now belong to a different agent."
    )


def merge_identity_change_note(profile: str, note: str) -> str:
    """Append ``note`` to the profile's identity-change section.

    Appends — never rewrites the rest of the profile (the agent's observations
    about its owner are not ours to edit, and losing them to a rename would be
    a worse bug than the one this fixes). Keeps a single section and the last
    ``MAX_IDENTITY_CHANGE_ENTRIES`` entries.

    The section is delimited by the NEXT ``##`` heading, not by end-of-text.
    ``update_awareness`` has the model rewrite the whole profile in the
    prescribed structure, so this section routinely sits in the MIDDLE; the
    first implementation treated everything after the marker as belonging to
    it and silently dropped the sections below on the next rename (review,
    2026-08-05). Whatever follows is carried through untouched.
    """
    body = (profile or "").rstrip()
    if IDENTITY_CHANGE_SECTION not in body:
        entries: List[str] = [note]
        head, tail = body, ""
    else:
        head, _, rest = body.partition(IDENTITY_CHANGE_SECTION)
        head = head.rstrip()
        # Cut at the next heading; everything from there on is someone else's.
        lines = rest.splitlines()
        cut = next(
            (i for i, ln in enumerate(lines) if ln.lstrip().startswith("## ")),
            len(lines),
        )
        entries = [
            ln.strip() for ln in lines[:cut] if ln.strip().startswith("- ")
        ]
        entries.append(note)
        tail = "\n".join(lines[cut:]).strip("\n")

    entries = entries[-MAX_IDENTITY_CHANGE_ENTRIES:]
    section = f"{IDENTITY_CHANGE_SECTION}\n" + "\n".join(entries) + "\n"

    parts = [p for p in (head, section.rstrip(), tail) if p]
    return "\n\n".join(parts) + "\n"


async def _same_owner_name_holder(
    db, *, owner_user_id: str, name: str, exclude_agent_id: str
) -> Optional[str]:
    """agent_id of another agent of the SAME owner already using ``name``.

    Scoped to the owner on purpose: two users naming their agents the same
    thing is not a conflict and must never be reported across accounts.
    """
    try:
        rows = await db.get("agents", {"created_by": owner_user_id})
        for row in rows or []:
            if row.get("agent_id") == exclude_agent_id:
                continue
            if (row.get("agent_name") or "").strip() == name:
                return row.get("agent_id")
    except Exception as e:  # noqa: BLE001 — advisory note, never blocking
        logger.debug(f"name-clash check failed for {owner_user_id}: {e}")
    return None


async def _record_identity_change(
    db, agent_id: str, old_name: str, new_name: str
) -> None:
    """File the rename into the agent's Awareness profile.

    Best-effort by design: the name change itself has already been written,
    and failing the tool afterwards would tell the model the rename did not
    happen. A missing note degrades to the old (buggy) behaviour, which is
    strictly better than reporting a false failure.
    """
    try:
        instances = await InstanceRepository(db).get_by_agent(
            agent_id=agent_id, module_class="AwarenessModule"
        )
        if not instances:
            logger.warning(
                f"_record_identity_change: no AwarenessModule instance for "
                f"{agent_id}; identity memory not corrected"
            )
            return
        instance_id = instances[0].instance_id
        awareness_repo = InstanceAwarenessRepository(db)
        current = await awareness_repo.get_by_instance(instance_id)
        profile = (current.awareness if current else "") or ""
        await awareness_repo.upsert(
            instance_id,
            merge_identity_change_note(
                profile, build_identity_change_note(old_name, new_name)
            ),
        )
    except Exception as e:  # noqa: BLE001 — see docstring
        logger.warning(f"_record_identity_change failed for {agent_id}: {e}")


async def update_agent_profile_from_args(
    db, agent_id: str, *, new_name: Optional[str] = None,
    new_description: Optional[str] = None,
) -> str:
    """Record the agent's display name and/or one-line peer description.

    Returns the SAME status string the update_agent_profile MCP tool has always
    produced — DirectStore and the backend twin route both call this, so the
    two paths are byte-identical. Every HANDLED outcome is a string the model
    reads; the repository calls (get_agent / update_agent) can still raise on a
    real db error (db down, a MySQL 1406) — DirectStore lets that propagate
    (as the old tool did), while the Http path degrades it to an "Error: …(500)"
    string, a real parity seam on unhandled db failures only.
    """
    if new_name is None and new_description is None:
        return (
            "Error: nothing to update — pass new_name and/or "
            "new_description."
        )

    repo = AgentRepository(db)

    agent = await repo.get_agent(agent_id)
    if not agent:
        return f"Error: Agent {agent_id} not found"

    updates: dict = {}
    old_name = normalize_agent_text(agent.agent_name)
    renamed_from: Optional[str] = None
    notes: List[str] = []

    if new_name is not None:
        wanted = normalize_agent_text(new_name)
        if not wanted:
            return "Error: new_name cannot be empty"
        # Bind to the SAME cap the read model enforces (Agent.agent_name
        # Field(max_length=AGENT_TEXT_MAX_LENGTH)) and the MySQL column
        # (VARCHAR(255)). Without it a >255 write succeeds on sqlite (TEXT) but
        # makes the row UNREADABLE (get_agent → Agent(...) ValidationError, the
        # NetMindAI-Open#71 bug) and diverges on MySQL (1406 / silent truncate).
        # Checked HERE — the one shared fn both stores call — so Direct and Http
        # reject identically (rule #6 / the store parity invariant).
        if len(wanted) > AGENT_TEXT_MAX_LENGTH:
            return f"Error: new_name is too long (max {AGENT_TEXT_MAX_LENGTH} characters)"
        if not agent_field_matches(agent, "agent_name", wanted):
            updates["agent_name"] = wanted
            renamed_from = old_name
            # Duplicate names are ALLOWED — the owner may deliberately hand a
            # name from one agent to another. What is forbidden is doing it
            # silently: two agents answering to one name is exactly how the
            # incident started, so name the current holder and let the agent
            # check with its owner.
            clash = await _same_owner_name_holder(
                db, owner_user_id=agent.created_by,
                name=wanted, exclude_agent_id=agent_id,
            )
            if clash:
                notes.append(
                    f"Note: 「{wanted}」 is currently also the name of "
                    f"{clash}, another agent of your owner. The rename "
                    f"was applied as asked — if that was not intended, "
                    f"ask your creator which agent should keep it."
                )

    if new_description is not None:
        wanted_desc = normalize_agent_text(new_description)
        # Same AGENT_TEXT_MAX_LENGTH cap the name branch and the read model
        # enforce — an over-long description would make the agent row unreadable
        # (see the name branch).
        if len(wanted_desc) > AGENT_TEXT_MAX_LENGTH:
            return (
                f"Error: new_description is too long "
                f"(max {AGENT_TEXT_MAX_LENGTH} characters)"
            )
        # Same equality short-circuit the name branch does, and for a sharper
        # reason: update_agent returns cursor.rowcount, which counts CHANGED
        # rows on MySQL (dev/prod) but MATCHED rows on SQLite. Re-saving an
        # identical description would therefore report "Error: the update did
        # not apply" on cloud only — for a write that was simply a no-op — and
        # the §5 prompt invites exactly those repeat calls (review 2026-08-05).
        if not agent_field_matches(agent, "agent_description", wanted_desc):
            updates["agent_description"] = wanted_desc

    if not updates:
        return (
            "No changes needed — the values you passed already match "
            "your current profile."
        )

    await repo.update_agent(agent_id, updates)

    # The row decides, not the rowcount. The value-equality short-circuit above
    # already removes the no-op case, but `cursor.rowcount` can still report 0
    # for a write that DID land (it counts CHANGED rows on MySQL), and reading
    # that as failure is precisely the bug the HTTP twin was fixed for — an
    # agent told "the update did not apply" would re-issue a rename that had
    # already happened. Same predicate as the comparison above, so "needed a
    # write" and "the write landed" cannot mean different things.
    stored = await repo.get_agent(agent_id)
    if stored is None or any(
        not agent_field_matches(stored, field, wanted)
        for field, wanted in updates.items()
    ):
        return "Error: the update did not apply; nothing was changed"

    # A rename is not complete until the memory that asserts the old identity
    # has been corrected (P1 section 02 ①).
    if renamed_from:
        await _record_identity_change(
            db, agent_id, renamed_from, updates["agent_name"]
        )

    # Peers must see this now, not after the next turn (P1 section 02 target 2).
    try:
        from xyz_agent_context.message_bus.agent_discovery_sync import (
            sync_agent_discovery,
        )
        await sync_agent_discovery(db, agent_id)
    except Exception as e:  # noqa: BLE001 — profile write already landed
        logger.warning(f"update_agent_profile: discovery sync failed: {e}")

    changed = ", ".join(sorted(updates))
    return " ".join([f"Profile updated successfully ({changed})."] + notes)
