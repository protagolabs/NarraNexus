"""
@file_name: profile_write.py
@author: NarraNexus
@date: 2026-08-18
@description: The agent-profile write transaction — the single writer of a
rename anywhere in the platform.

Renaming an agent is three writes, not one: the `agents` row, the identity
correction inside the Awareness profile, and the peer-discovery row. Shenzhen
round 2 (prod agent_4a0ae5f40af2) is what a caller doing a subset looks like —
the UI rename wrote the column while the profile kept a platform-voiced record
asserting the PREVIOUS name, so the agent read it and introduced itself as
「美食家」 for a row that said 「小绿」. Four writers each remembering a different
subset is a standing invitation to that bug; there is one writer now.

Why it lives HERE and not in awareness_module, where it was first written: the
transaction writes the `agents` table, refreshes `bus_agent_registry`, and takes
`is_public` / `created_by` — none of which is an Awareness concern. Awareness is
one STEP of it. Having core routes import a hot-pluggable Module inverted 铁律
#3: unregistering AwarenessModule became an ImportError at route import, i.e.
the backend does not start, rather than one feature degrading. The identity note
is reached through a DEFERRED import for the same reason — without Awareness the
note step degrades to a warning, which is what "hot-pluggable" has to mean.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from loguru import logger

from xyz_agent_context.repository import AgentRepository
from xyz_agent_context.schema import (
    AGENT_TEXT_MAX_LENGTH,
    agent_field_matches,
    normalize_agent_text,
)


def _awareness_identity_writers():
    """Awareness's two identity-record writers, or None when it is not loaded.

    Returns the module rather than dispatching by name: a typo in a dispatch key
    is a runtime KeyError on a path with no try/except around it, and callers
    keep their real signatures and types.

    Be precise about what deferring buys, because the first version of this
    docstring claimed more than it delivers. Python imports parent packages, so
    this import runs ``xyz_agent_context.module.__init__`` and with it the whole
    MODULE_MAP — measured: 22 sibling module packages. What deferring achieves
    is that *this package* and the routes above it hold no module-scope
    dependency on the Module layer, so the import graph says who owns the
    transaction. It is not import-time isolation, and claiming it was would be
    the third overstated claim in this change.

    Nor does the ImportError branch carry the hot-plug story. Unregistering
    AwarenessModule from MODULE_MAP leaves the package on disk and the import
    still succeeds; the degradation that actually happens is that the agent has
    no AwarenessModule instance, which both writers already answer with False.
    This guard is only for a deployment shipping without the package at all.
    """
    try:
        from xyz_agent_context.module import awareness_module
    except ImportError:
        logger.warning(
            "[agent-profile-write] AwarenessModule unavailable; the identity "
            "record was not touched"
        )
        return None
    return awareness_module


async def _record_identity(db, agent_id: str, old_name: str, new_name: str) -> bool:
    aw = _awareness_identity_writers()
    if aw is None:
        return False
    return await aw.record_identity_change(db, agent_id, old_name, new_name)


async def _reconcile_identity(db, agent_id: str, current_name: str) -> Optional[bool]:
    aw = _awareness_identity_writers()
    if aw is None:
        return None
    return await aw.reconcile_identity_record(db, agent_id, current_name)


@dataclass(frozen=True)
class AgentProfileWrite:
    """What a profile write actually did, for callers that are not the model.

    ``update_agent_profile_from_args`` renders this into the sentence the MCP
    tool has always returned. Every OTHER caller is an HTTP route that owes its
    client a status code, and inferring one by matching on English prose is a
    coupling that breaks the first time the wording is improved — so the facts
    are carried structurally and the prose is derived from them, never parsed.
    """

    #: Spelled out rather than left as ``str``: a caller comparing against a
    #: typo'd literal type-checks fine and then silently takes the fallback
    #: branch, handing a model-facing sentence to a UI.
    status: Literal["updated", "unchanged", "error"]
    #: Which of ``agent_name`` / ``agent_description`` / extras were written.
    #: Non-empty only when ``status == "updated"``.
    updated_fields: tuple[str, ...] = ()
    #: Fields the write was supposed to land and the re-read did not confirm.
    #: Kept separate from ``updated_fields`` rather than overloading it: a
    #: caller adding write telemetry would otherwise read the failure list as
    #: "what we wrote" without checking ``ok``, and count failures as writes —
    #: an error that never raises and only makes a metric quietly optimistic.
    unapplied_fields: tuple[str, ...] = ()
    #: The previous name, set only when this write renamed the agent.
    renamed_from: Optional[str] = None
    #: The name now stored, set only when this write renamed the agent — so it
    #: is None for a normalization repair, which writes agent_name without the
    #: name having changed. Tied to ``renamed_from``, never to "was it written":
    #: the field name reads as "did this rename", and a caller testing it that
    #: way would get a false positive on every legacy unnormalized row.
    renamed_to: Optional[str] = None
    #: agent_id of another agent of the same owner now sharing the new name.
    name_clash_with: Optional[str] = None
    #: Did this call correct a platform record that named the wrong name
    #: without any rename happening? Separate from ``identity_note_recorded``,
    #: which reports the note for a rename THIS call performed — a caller asking
    #: "did this rename" must not be answered "yes" by a repair.
    #: ``None`` = nothing needed repairing; ``True`` = a stale record was
    #: corrected; ``False`` = one was found and the correction FAILED, which is
    #: the state the incident was and must not read as "fine".
    identity_reconciled: Optional[bool] = None
    #: Did the Awareness identity correction actually land? Only meaningful when
    #: ``renamed_from`` is set. The write is best-effort on purpose (the name is
    #: already stored; failing the caller afterwards would report a rename that
    #: happened as one that did not) — but that silent degradation IS the
    #: incident this transaction exists for, so it must at least be visible.
    identity_note_recorded: bool = False
    #: Machine-readable failure, so routes can map it to a status code. Same
    #: reason as ``status`` for pinning the spellings in the type.
    error_kind: Optional[
        Literal[
            "nothing_to_update", "not_found", "empty_name", "too_long",
            "not_applied",
        ]
    ] = None
    #: The same failure as a sentence written for a model to read.
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status != "error"


def _stored_text_is_unnormalized(agent, field: str) -> bool:
    """Does the row still hold text that normalization would change?

    ``agent_field_matches`` compares the NORMALIZED forms, so a row written
    before normalization existed (or by a pre-fix bundle import) holding
    `"  old  "` reads as already equal to the `"old"` it would be rewritten to.
    The write is suppressed, the row keeps the unstripped value, and every
    future comparison answers the same way — that row can never be renamed
    again. The manyfold upsert wrapper used to be the one place that repaired
    such rows on the way past; folding that path into this transaction moved the
    obligation here, which also extends it from one caller to all of them.

    Deliberately NOT merged into ``agent_field_matches``: that predicate answers
    "are these the same value", which is the right question for the caller
    deciding whether it got what it asked for. This one answers "would writing
    change the bytes in the row", and only the writer needs it.
    """
    stored = getattr(agent, field, None) or ""
    return stored != normalize_agent_text(stored)


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


async def apply_agent_profile_change(
    db, agent_id: str, *, new_name: Optional[str] = None,
    new_description: Optional[str] = None,
    extra_updates: Optional[dict] = None,
) -> AgentProfileWrite:
    """Write an agent's name and/or description as ONE transaction.

    This is the whole rename obligation in one place, and it is the reason the
    function exists rather than each caller writing the column itself: a rename
    is not "set a column", it is set-the-column-AND-correct-the-memory-AND-
    refresh-the-directory. Shenzhen round 2 (prod agent_4a0ae5f40af2) is what a
    caller doing only the first part looks like — the UI rename wrote the name
    while the Awareness profile kept a platform-voiced record asserting the
    PREVIOUS name, so the agent read it and introduced itself as 「美食家」 for a
    row that said 「小绿」. Three writers each remembering three steps is a
    standing invitation to that bug; there is now one writer and no steps to
    remember.

    ``extra_updates`` carries fields with no identity semantics (``is_public``)
    so a caller that edits them alongside the name still issues a SINGLE row
    write — splitting it would open a window where the row is half-updated.
    """
    if new_name is None and new_description is None and not extra_updates:
        return AgentProfileWrite(
            status="error",
            error_kind="nothing_to_update",
            error=(
                "Error: nothing to update — pass new_name and/or "
                "new_description."
            ),
        )

    repo = AgentRepository(db)

    agent = await repo.get_agent(agent_id)
    if not agent:
        return AgentProfileWrite(
            status="error",
            error_kind="not_found",
            error=f"Error: Agent {agent_id} not found",
        )

    # Extras go through the SAME value-equality short-circuit as name and
    # description. Skipping it would make "set the value it already holds"
    # issue a write — harmless on its own, but it is what the dialect-
    # independence of this function rests on: a write that changes nothing
    # returns rowcount 0 on MySQL and 1 on SQLite, and everything downstream
    # is built on never having to interpret that number.
    updates: dict = {
        field: wanted
        for field, wanted in (extra_updates or {}).items()
        if not agent_field_matches(agent, field, wanted)
    }
    old_name = normalize_agent_text(agent.agent_name)
    renamed_from: Optional[str] = None
    clash: Optional[str] = None

    if new_name is not None:
        wanted = normalize_agent_text(new_name)
        if not wanted:
            return AgentProfileWrite(
                status="error",
                error_kind="empty_name",
                error="Error: new_name cannot be empty",
            )
        # Bind to the SAME cap the read model enforces (Agent.agent_name
        # Field(max_length=AGENT_TEXT_MAX_LENGTH)) and the MySQL column
        # (VARCHAR(255)). Without it a >255 write succeeds on sqlite (TEXT) but
        # makes the row UNREADABLE (get_agent → Agent(...) ValidationError, the
        # NetMindAI-Open#71 bug) and diverges on MySQL (1406 / silent truncate).
        # Checked HERE — the one shared fn every caller reaches — so the MCP
        # tool, both stores and both HTTP routes reject identically.
        if len(wanted) > AGENT_TEXT_MAX_LENGTH:
            return AgentProfileWrite(
                status="error",
                error_kind="too_long",
                error=(
                    f"Error: new_name is too long "
                    f"(max {AGENT_TEXT_MAX_LENGTH} characters)"
                ),
            )
        # Two different questions, and conflating them costs either the repair
        # or a bogus identity record: "is this a rename" decides whether the
        # agent's memory must be corrected, while "would this write change the
        # row" decides whether to write at all. A stored `"  old  "` normalizes
        # to the same value it would be rewritten to, so it is NOT a rename —
        # but it still needs the write, or the row stays unrenameable forever.
        is_rename = not agent_field_matches(agent, "agent_name", wanted)
        if is_rename or _stored_text_is_unnormalized(agent, "agent_name"):
            updates["agent_name"] = wanted
        if is_rename:
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

    if new_description is not None:
        wanted_desc = normalize_agent_text(new_description)
        # Same AGENT_TEXT_MAX_LENGTH cap the name branch and the read model
        # enforce — an over-long description would make the agent row unreadable
        # (see the name branch).
        if len(wanted_desc) > AGENT_TEXT_MAX_LENGTH:
            return AgentProfileWrite(
                status="error",
                error_kind="too_long",
                error=(
                    f"Error: new_description is too long "
                    f"(max {AGENT_TEXT_MAX_LENGTH} characters)"
                ),
            )
        # Same equality short-circuit the name branch does, and for a sharper
        # reason: update_agent returns cursor.rowcount, which counts CHANGED
        # rows on MySQL (dev/prod) but MATCHED rows on SQLite. Re-saving an
        # identical description would therefore report "Error: the update did
        # not apply" on cloud only — for a write that was simply a no-op — and
        # the §5 prompt invites exactly those repeat calls (review 2026-08-05).
        if not agent_field_matches(
            agent, "agent_description", wanted_desc
        ) or _stored_text_is_unnormalized(agent, "agent_description"):
            updates["agent_description"] = wanted_desc

    if not updates:
        # Nothing to write, but the directory is still refreshed below: sync
        # swallows its own failures, so a peer directory left on the old name
        # has no other way back — and re-saving the same values is the most
        # natural way a user retries after being told the save failed. That
        # retry must not be the one path that skips the repair (#320).
        await _refresh_peer_directory(db, agent_id)
        # A caller that named a name is entitled to have the record agree with
        # it, even when the column already did. This branch is where the ticket's
        # own agent lands — row and request both 「小绿」, record still asserting
        # 「美食家」 — and returning success without looking was the reason the fix
        # did not repair the population it was written for.
        reconciled = None
        if new_name is not None:
            reconciled = await _reconcile_identity(
                db, agent_id, normalize_agent_text(agent.agent_name)
            )
        return AgentProfileWrite(
            status="unchanged", identity_reconciled=reconciled
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
    unapplied = (
        list(updates)
        if stored is None
        else [
            field
            for field, wanted in updates.items()
            if not agent_field_matches(stored, field, wanted)
        ]
    )
    if unapplied:
        # Leave a trace. Without one, the only record that this happened is the
        # sentence the model was handed — and if a concurrent writer caused it,
        # nothing in the logs or the DB can be lined up with it afterwards.
        # WARNING, not ERROR: there is no CAS across read-write-reread, so a
        # second tab or the agent's own tool landing inside that window shows
        # up here as benign last-write-wins, which must not page anyone.
        logger.warning(
            f"[agent-profile-write] {agent_id} does not hold the requested "
            f"values for {unapplied} after the write — concurrent overwrite, "
            f"or the write did not land"
        )
        # Refresh anyway. dev's promise is "every accepted request republishes",
        # and it republished BEFORE verifying the write — a concurrent overwrite
        # is exactly a case where the directory may be stale, and this is the
        # one path that would otherwise skip the repair (#320's argument).
        await _refresh_peer_directory(db, agent_id)
        return AgentProfileWrite(
            status="error",
            error_kind="not_applied",
            error="Error: the update did not apply; nothing was changed",
            unapplied_fields=tuple(sorted(unapplied)),
        )

    # A rename is not complete until the memory that asserts the old identity
    # has been corrected (P1 section 02 ①). Unconditional here, for every
    # caller: the note only ever pointed the right way because the agent's own
    # tool happened to be the writer, and Shenzhen round 2 is what the other
    # writers produced — a stale correction is worse than none, because it
    # speaks in the platform's voice and the agent believes it.
    note_recorded = False
    reconciled = None
    # ``is not None``, not truthiness: renamed_from carries the PREVIOUS name,
    # and a legacy row can hold "". Folding that into "did not rename" sent a
    # first naming down the reconcile path with an empty old name, producing a
    # record reading "You are 「」" — which asserts nothing the reader can parse,
    # so it failed to supersede the stale record and sat beside it. Exactly the
    # self-contradicting prompt this transaction exists to prevent.
    if renamed_from is not None:
        note_recorded = await _record_identity(
            db, agent_id, renamed_from, updates["agent_name"]
        )
        if not note_recorded:
            # One greppable line for the state that IS the incident: the column
            # moved, the memory did not. Without it the two halves live in
            # separate log records inside record_identity_change and nothing
            # says the rename they belong to completed anyway.
            logger.warning(
                f"[agent-profile-write] {agent_id} renamed "
                f"{renamed_from!r} → {updates['agent_name']!r} but the identity "
                f"correction did not land — the agent will keep introducing "
                f"itself by the old name"
            )
    elif new_name is not None:
        # Wrote something (a description, a normalization repair) without
        # renaming — the record can still be stale, and the same entitlement
        # applies as in the no-op branch above.
        reconciled = await _reconcile_identity(
            db, agent_id, normalize_agent_text(agent.agent_name)
        )

    # Peers must see this now, not after the next turn (P1 section 02 target 2).
    await _refresh_peer_directory(db, agent_id)

    return AgentProfileWrite(
        status="updated",
        updated_fields=tuple(sorted(updates)),
        renamed_from=renamed_from,
        renamed_to=updates.get("agent_name") if renamed_from is not None else None,
        name_clash_with=clash,
        identity_note_recorded=note_recorded,
        identity_reconciled=reconciled,
    )


async def _refresh_peer_directory(db, agent_id: str) -> None:
    """Republish the agent's discovery row; never raise into the caller.

    The profile write has already landed by the time this runs, so a failure
    here may not turn into "the rename failed" — it is logged and swallowed,
    exactly as ``sync_agent_discovery`` does internally.
    """
    try:
        from xyz_agent_context.message_bus.agent_discovery_sync import (
            sync_agent_discovery,
        )
        if not await sync_agent_discovery(db, agent_id):
            logger.warning(
                f"[agent-profile-write] peer-directory sync failed for "
                f"{agent_id}; peers may still see the previous name"
            )
    except Exception as e:  # noqa: BLE001 — profile write already landed
        logger.warning(f"[agent-profile-write] discovery sync failed: {e}")
