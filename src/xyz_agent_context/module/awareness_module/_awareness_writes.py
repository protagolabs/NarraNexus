"""
@file_name: _awareness_writes.py
@author:
@date: 2026-08-10
@description: The Awareness half of a rename — the identity record the agent
reads about itself — plus the MCP tool's renderer.

The rename TRANSACTION itself lives in ``xyz_agent_context.agent_profile``
(moved 2026-08-18; it writes the `agents` row and the peer directory, neither of
which is an Awareness concern). What stays here is what genuinely belongs to
Awareness, because it is written into ``instance_awareness``:

* the ``## Identity Changes (platform record)`` section — its constants, the two
  note builders, the reader that lets a new record supersede a stale one, and
  the merge that keeps the rest of the profile intact;
* ``record_identity_change`` / ``reconcile_identity_record``, the two writers
  the transaction calls (public, not underscore-private: they are called across
  a package boundary now);
* ``retire_self_name``, which rewrites the agent's own name line — the one place
  the platform edits agent-authored text, and only ever the name (rule #15: a
  machine-knowable fact is derived, not left to the model to remember);
* ``update_agent_profile_from_args``, the MCP tool's renderer, which calls the
  transaction and formats its result into the sentence the tool has always
  returned. DirectStore (local) and the backend twin route (cloud) both call it,
  so the two stay byte-identical.

Why any of this exists: P1 section 02 ①, prod evt_1f9c6680 — writing
`agents.agent_name` alone left the agent's free-text long-term memory still
asserting the old identity, and the old name may by then belong to a different
agent of the same owner. A column change cannot move a few thousand words of
narrative; a record the agent reads every turn can.
"""
from __future__ import annotations

import re
from typing import List, Optional

from loguru import logger

from xyz_agent_context.repository import (
    InstanceAwarenessRepository,
    InstanceRepository,
)
from xyz_agent_context.schema import (
    normalize_agent_text,
)

# Where a rename records itself inside the Awareness profile. The profile is
# injected verbatim into the system prompt every turn, so this is the one place
# a correction is guaranteed to be read. (See module docstring for the incident.)
IDENTITY_CHANGE_SECTION = "## Identity Changes (platform record)"

# Keep the section bounded: renames are rare, but an unbounded log would eat
# the context window it lives in. Newest entries win.
MAX_IDENTITY_CHANGE_ENTRIES = 5


def _for_note(name: str) -> str:
    """A name rendered so the record stays readable back.

    The whole supersede mechanism round-trips through ``You are 「X」``, but a
    name is only ``strip()``-ed on the way into the row — it may legally contain
    ``」`` or a newline. ``」`` truncates the read-back, so every later call sees
    "the record disagrees with the row", rewrites the profile and logs a
    correction that never converges. A newline is worse: the tail becomes a
    separate ``- `` entry, and if it happens to contain the marker phrase it is
    read as the current assertion — a forged platform record that then prunes
    the real one.

    BOTH brackets are escaped, not just the closing one. A name may contain the
    marker phrase itself — ``小绿\n- 2026: You are 「冒充」`` — and with only ``」``
    escaped that forged assertion still parses, and being earlier in the line it
    wins: the record then claims a name its owner chose for it. Neutralising
    ``「`` too means no complete ``You are 「…」`` can occur inside a name, so the
    only one carrying real brackets is the one this builder wrote.

    Escaped rather than rejected at the write edge on purpose: refusing these
    names would leave any existing row holding one permanently unrenameable,
    which is the same class of trap as the normalization repair.
    """
    return (
        (name or "")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("「", "﹁")
        .replace("」", "﹂")
    )


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
    old_name, new_name = _for_note(old_name), _for_note(new_name)
    return (
        f"- {when}: renamed by your creator from 「{old_name}」 to 「{new_name}」. "
        f"You are 「{new_name}」. 「{old_name}」 is no longer your name — if it "
        f"appears in your memories or past conversations, that is history, and "
        f"it may now belong to a different agent."
    )


# Every note states the current name exactly once, in this shape. The builder
# below writes it and ``identity_note_asserts`` reads it back, deliberately
# adjacent: the moment those two drift, reconciliation silently stops finding
# stale records and the section goes back to contradicting the row.
_ASSERTS_NAME = re.compile(r"You are 「([^」]+)」")


def identity_note_asserts(entry: str) -> Optional[str]:
    """The name an identity entry tells the agent it currently has."""
    m = _ASSERTS_NAME.search(entry or "")
    return m.group(1) if m else None


def build_identity_reconciliation_note(
    current_name: str, stale_name: str, when: Optional[str] = None
) -> str:
    """Correct a platform record that names the wrong agent name.

    Distinct wording from a rename on purpose: nothing was renamed just now, and
    telling the agent it "was renamed" when its owner did no such thing is a
    false statement in the one section whose whole value is that the agent
    believes it. This says only what is true — the record was wrong, and here is
    the name the platform holds.
    """
    if when is None:
        from datetime import datetime, timezone
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # "You are 「X」" is not a wording choice — it is the shape
    # ``identity_note_asserts`` reads, and therefore what lets this note
    # supersede the stale one it is correcting. The first draft said "Your name
    # is 「X」", which parsed as asserting nothing, so the record it was written
    # to replace survived beside it. Any future note MUST contain this phrase;
    # ``test_every_identity_note_states_the_current_name_readably`` enforces it.
    current_name, stale_name = _for_note(current_name), _for_note(stale_name)
    return (
        f"- {when}: platform record corrected. You are 「{current_name}」. "
        f"An earlier record here said 「{stale_name}」 — that record was stale, "
        f"not a rename, and 「{stale_name}」 is not your name."
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
        # Drop entries this note supersedes — every one that tells the agent it
        # is called something other than what this note says it is called. The
        # incident proved a single platform-voiced line is enough for the agent
        # to introduce itself by the wrong name and defend it; leaving three
        # mutually exclusive ones and trusting the model to prefer the last is
        # the same bet with worse odds. Nothing is lost: the surviving note
        # names the previous name itself ("renamed from X to Y").
        now_called = identity_note_asserts(note)
        if now_called:
            entries = [
                e for e in entries
                if identity_note_asserts(e) in (None, now_called)
            ]
        entries.append(note)
        tail = "\n".join(lines[cut:]).strip("\n")

    entries = entries[-MAX_IDENTITY_CHANGE_ENTRIES:]
    section = f"{IDENTITY_CHANGE_SECTION}\n" + "\n".join(entries) + "\n"

    parts = [p for p in (head, section.rstrip(), tail) if p]
    return "\n\n".join(parts) + "\n"


def _note_is_readable(note: str, expected_name: str) -> bool:
    """Refuse to file a record whose own assertion cannot be read back.

    The supersede step keys on ``identity_note_asserts``; a note it cannot parse
    is appended beside the record it was meant to replace, leaving the prompt
    holding both. That already happened once, when the reconciliation note was
    first phrased "Your name is 「X」". A wording change should fail loudly here
    rather than silently stop superseding.
    """
    got = identity_note_asserts(note)
    if got == _for_note(expected_name):
        return True
    logger.warning(
        f"[identity-note] refusing to file a record that asserts {got!r} "
        f"instead of {expected_name!r} — the note wording no longer round-trips"
    )
    return False


# A self-name declaration: an optional bullet, a "name" label in either
# language, a separator, then the value. Deliberately narrow — it must not match
# prose. The agent writes this line itself (``update_awareness`` has the model
# rewrite the whole profile in the prescribed structure), so the label wording
# is the model's, and this covers the spellings the prompt's structure produces.
_SELF_NAME_LINE = re.compile(
    r"^(?P<head>[-*\s]*(?:名称|名字|姓名|Name|name|NAME)\s*[：:]\s*)(?P<value>.*)$"
)


# What may follow the name on a declaration line: nothing, or a separator that
# starts a description. A SPACE does not qualify — `- name: 美食家 是 owner 最近
# 常去的那家店` is an owner observation that merely opens with the marker, and
# rewriting it would be the content loss this whole area promises never to cause.
_NAME_ENDERS = ("", "；", ";", "，", ",", "、", "。", ".", "|", "/", "-", "—", "(", "（")

# The prescribed profile has FOUR numbered sections and only the fourth is about
# the agent: 1-3 record the owner's preferences and observations. An owner
# recorded as `- 姓名：张三` under Communication Style, for an agent that also
# happened to be called 张三, would otherwise have their name replaced by the
# agent's new one — silently, and unrecoverably, since instance_awareness is
# overwritten by upsert.
#
# Excluded by NEGATION rather than by matching section 4's title: the model
# writes these headings and they drift, and a positive match would make
# retirement stop working the moment one did — silently, which is the failure
# mode this whole change exists to remove. Skipping 1/2/3 keeps the owner's
# sections protected while a renamed or renumbered identity section still works.
_OWNER_SECTION = re.compile(r"^\s*##\s*[123]\s*[.、]")
_ANY_H2 = re.compile(r"^\s*##\s+")


def _ends_the_name(rest: str) -> bool:
    return rest == "" or rest[0] in _NAME_ENDERS


def retire_self_name(profile: str, old_name: str, new_name: str) -> str:
    """Rewrite the agent's own "my name is X" line to the name it now has.

    Why the platform touches agent-authored text at all, when 2026-08-04
    established that it must not: that principle protects the agent's
    OBSERVATIONS — what it has learned about its owner — and losing those to a
    rename would be worse than the bug being fixed. Its own name is a different
    thing: machine-knowable, owned by the ``agents`` row, and 铁律 #15 says a
    machine-knowable fact is derived, never left to the model to remember.

    Measured 2026-08-19 UTC: with the row, BasicInfo, the identity record and the
    peer directory all corrected, a real two-turn run still answered with the
    old name — the profile said ``- 名称：美食家`` above the correction, and the
    model followed the line that came first.

    Only the VALUE of a name-declaration line is touched, and only where the old
    name IS that value (optionally followed by a separator that opens a
    description). An owner observation that merely starts with the marker — and
    then keeps talking — is left exactly as written.

    Scoped to the agent's own section: the prescribed profile puts the owner's
    preferences and observations in sections 1-3 and the agent's identity in 4,
    and a name in an owner section is the OWNER's.

    Known limit, stated rather than papered over: the label set is the spellings
    this prompt's structure produces in Chinese and English. A profile the model
    wrote in another language keeps its stale self-name line, and the identity
    record below it is then the only correction. Widening the set is easy; making
    it exhaustive is not, which is why the record is the backstop and this is the
    optimisation.
    """
    old, new = (old_name or "").strip(), (new_name or "").strip()
    if not old or old == new:
        return profile

    out, in_owner_section = [], False
    for line in (profile or "").splitlines():
        if _ANY_H2.match(line):
            in_owner_section = bool(_OWNER_SECTION.match(line))
        m = None if in_owner_section else _SELF_NAME_LINE.match(line)
        value = m.group("value").strip() if m else ""
        if m and value.startswith(old) and _ends_the_name(value[len(old):]):
            rewritten = m.group("head") + new + value[len(old):]
            # The one place the platform edits an agent's own long-term memory.
            # An edit nobody can see afterwards is an edit nobody can undo:
            # instance_awareness is overwritten by upsert, so the previous line
            # exists in no other record once this returns.
            logger.info(
                f"[identity] retiring self-name line: {line.strip()!r} -> "
                f"{rewritten.strip()!r}"
            )
            out.append(rewritten)
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if profile.endswith("\n") else "")


async def record_identity_change(
    db, agent_id: str, old_name: str, new_name: str
) -> bool:
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
                f"record_identity_change: no AwarenessModule instance for "
                f"{agent_id}; identity memory not corrected"
            )
            return False
        instance_id = instances[0].instance_id
        awareness_repo = InstanceAwarenessRepository(db)
        current = await awareness_repo.get_by_instance(instance_id)
        profile = (current.awareness if current else "") or ""
        note = build_identity_change_note(old_name, new_name)
        if not _note_is_readable(note, new_name):
            return False
        # Retire the agent's own "my name is X" line BEFORE appending the
        # record, so the profile is never left with one section naming the old
        # name and another the new one — which is precisely the state a real
        # two-turn run answered the old name from (2026-08-19).
        profile = retire_self_name(profile, old_name, new_name)
        await awareness_repo.upsert(
            instance_id, merge_identity_change_note(profile, note)
        )
        return True
    except Exception as e:  # noqa: BLE001 — see docstring
        logger.warning(f"record_identity_change failed for {agent_id}: {e}")
        return False


async def reconcile_identity_record(
    db, agent_id: str, current_name: str
) -> Optional[bool]:
    """Correct the profile when it asserts a name the row does not hold.

    The note used to be written whenever THIS call changed the column, which
    left every already-diverged agent unrepairable: prod agent_4a0ae5f40af2 has
    the row at 「小绿」 and a record asserting 「美食家」, so an owner renaming it to
    「小绿」 takes the "no changes needed" path, is told it worked, and reads the
    same wrong record on the next turn. That is #320's shape — retry, same
    answer, nothing moves — one layer down.

    So the obligation belongs to the STATE, not to the write: if the record
    disagrees with the row, correct it, whether or not this call wrote anything.
    Three-valued on purpose. ``None`` means there was nothing to repair — no
    record, or one that already agrees with the row — while ``False`` means a
    stale record was found and correcting it FAILED. Collapsing those into one
    boolean makes "this agent was fine" and "this agent is still broken"
    indistinguishable, and the second is the state the incident was.
    """
    try:
        instances = await InstanceRepository(db).get_by_agent(
            agent_id=agent_id, module_class="AwarenessModule"
        )
        if not instances:
            return None
        instance_id = instances[0].instance_id
        repo = InstanceAwarenessRepository(db)
        current = await repo.get_by_instance(instance_id)
        profile = (current.awareness if current else "") or ""
        if IDENTITY_CHANGE_SECTION not in profile:
            # Nothing has ever asserted a name here, so nothing contradicts the
            # row. An agent that was never renamed must not be handed a note.
            return None

        _, _, rest = profile.partition(IDENTITY_CHANGE_SECTION)
        lines = rest.splitlines()
        cut = next(
            (i for i, ln in enumerate(lines) if ln.lstrip().startswith("## ")),
            len(lines),
        )
        entries = [
            ln.strip() for ln in lines[:cut] if ln.strip().startswith("- ")
        ]
        asserted = next(
            (
                name
                for name in (identity_note_asserts(e) for e in reversed(entries))
                if name
            ),
            None,
        )
        # Compare against the ESCAPED form, because that is what a record
        # holds: a name containing 「」 or a newline is stored escaped so it can
        # be read back at all. Comparing against the raw name would find them
        # unequal forever — every call would "correct" the record and rewrite
        # the profile, converging on nothing and logging a warning each time.
        if asserted is None or asserted == _for_note(current_name):
            return None

        logger.warning(
            f"[agent-profile-write] {agent_id}: identity record asserted "
            f"{asserted!r} while the row holds {current_name!r} — correcting"
        )
        note = build_identity_reconciliation_note(current_name, asserted)
        if not _note_is_readable(note, current_name):
            return False
        # The same retirement the rename path does. This branch exists FOR the
        # already-diverged population — the ticket's own agent — so leaving its
        # self-name line stale would miss exactly the agents it was written for.
        # ``asserted`` is what the stale record claimed, which is the name that
        # line will be carrying.
        profile = retire_self_name(profile, asserted, current_name)
        await repo.upsert(instance_id, merge_identity_change_note(profile, note))
        return True
    except Exception as e:  # noqa: BLE001 — the profile write already landed
        logger.warning(f"reconcile_identity_record failed for {agent_id}: {e}")
        return False  # found stale, failed to fix — NOT "nothing to do"


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

    This is now a renderer over ``apply_agent_profile_change``: the transaction
    is shared with the HTTP rename routes so the identity correction cannot be
    skipped by whichever caller happens to write the column.
    """
    from xyz_agent_context.agent_profile import apply_agent_profile_change

    result = await apply_agent_profile_change(
        db, agent_id, new_name=new_name, new_description=new_description,
    )

    if result.error is not None:
        return result.error

    if result.status == "unchanged":
        return (
            "No changes needed — the values you passed already match "
            "your current profile."
        )

    notes: List[str] = []
    if result.name_clash_with:
        notes.append(
            f"Note: 「{result.renamed_to}」 is currently also the name of "
            f"{result.name_clash_with}, another agent of your owner. The "
            f"rename was applied as asked — if that was not intended, ask "
            f"your creator which agent should keep it."
        )

    changed = ", ".join(result.updated_fields)
    return " ".join([f"Profile updated successfully ({changed})."] + notes)
