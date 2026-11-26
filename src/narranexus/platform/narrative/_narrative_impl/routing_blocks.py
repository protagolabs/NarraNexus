"""
@file_name: routing_blocks.py
@date: 2026-08-26
@description: The prompt blocks narrative routing renders — ONE definition each,
shared by the continuity tier and the LLM judge — plus the read-side clamp the
merged decision request also uses.

Why shared: two tiers describe the same things to a model (the anchored
thread, the previous turn, the BM25 menu, the PARTICIPANT threads), and every
time one description was copied instead of shared the copies drifted and only
one got fixed.

Byte-identity is the contract: both prompts are pinned by measured numbers,
so `test_routing_blocks.py` pins every rendering byte for byte.

`clamp_head` shortens what a request SHOWS, never what is stored, and keeps
the head: the referent of a follow-up ("the first one") sits at the start of
the agent's previous reply.
"""

from __future__ import annotations

from typing import NamedTuple, Optional, Sequence, Tuple, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ..models import Narrative


class Rendered(NamedTuple):
    """A rendered prompt block — the one return shape of every renderer."""

    text: str


#: Appended in place of what was cut, so the model can tell a clamped block from
#: a short one — an invisible clamp reads as a complete (and false) statement.
TRUNCATION_MARKER = "…[truncated]"


def clamp_head(text: str, max_chars: Optional[int]) -> Tuple[str, bool]:
    """Keep the first ``max_chars`` characters. ``None`` means "do not clamp"."""
    if not text:
        return "", False
    if max_chars is None or len(text) <= max_chars:
        return text, False
    return text[:max_chars] + TRUNCATION_MARKER, True


# ── the thread the conversation is already on ───────────────────────────────

CONTINUITY_ANCHOR_HEADER = "Current Narrative Information:"
CONTINUITY_ANCHOR_ABSENT_NOTE = (
    "\nNo current Narrative information (this is a new session or no history)\n"
)
#: Continuity's closing sentence about legacy containers.
_LEGACY_BUCKET_NOTE = (
    "\nNote: If this is a [Special Default Narrative], its boundaries are very "
    "strict. Once the user mentions specific objects, tasks, or ongoing topics, "
    "it should be judged as not belonging to the current Narrative.\n"
)


def render_anchor_context(narrative: Optional["Narrative"]) -> Rendered:
    """The anchored thread's identity, as a model reads it.

    ``description`` appears only while the thread has no real summary yet — see
    ``Narrative.description_if_unsummarised``. Once a summary exists the
    description is a frozen creation-time prompt (prod: up to 198,398 chars)
    asserting a topic in the present tense that the thread may have left long
    ago, and the LABEL goes with it: an empty ``- Description:`` reads to the
    model as "this thread has no description", a different claim from not
    mentioning it.
    """
    if narrative is None:
        return Rendered(CONTINUITY_ANCHOR_ABSENT_NOTE)

    info = narrative.narrative_info
    label = (
        "[Special Default Narrative]"
        if narrative.is_special == "default"
        else "[Regular Narrative]"
    )
    birth_certificate = narrative.description_if_unsummarised()
    description_line = (
        f"\n- Description: {birth_certificate}" if birth_certificate else ""
    )
    keywords = (
        ", ".join(narrative.topic_keywords) if narrative.topic_keywords else "None"
    )
    return Rendered(
        f"\n{CONTINUITY_ANCHOR_HEADER}\n"
        f"{label}\n"
        f"- Name: {info.name}{description_line}\n"
        f"- Current Summary: {info.current_summary}\n"
        f"- Topic Keywords: {keywords}\n"
        f"{_LEGACY_BUCKET_NOTE}"
    )


# ── the previous turn ───────────────────────────────────────────────────────


def render_previous_turn(previous_query: str, previous_response: str) -> Rendered:
    """Whatever the user last SAW in their chat box.

    Two shapes, and the second one is not an edge case:
      * normal — the user asked X, the agent replied Y;
      * proactive — the agent messaged the user unprompted (a scheduled job),
        so there is no prior user query and a short reply ("好" / "yes") is
        almost certainly answering THAT message. The prompt says so explicitly
        rather than leaving the model to infer it from an empty field.
    """
    query = previous_query or ""
    response = previous_response or ""
    if query:
        return Rendered(
            f"Previous conversation turn:\n"
            f"User asked: {query}\n"
            f"Agent's reply/reasoning: {response}"
        )
    return Rendered(
        "Previous turn (the agent messaged the user proactively — "
        "there was no prior user query; the user's current message is "
        "most likely replying to this):\n"
        f"Agent said to user: {response}"
    )


# ── the BM25 menu ──────────────────────────────────────────────────────────

JUDGE_MENU_HEADER = "## Existing Topics:"
JUDGE_MENU_EMPTY_NOTE = (
    "(none — this user has no existing topics that overlap this message)\n\n"
)


def render_search_candidates(
    candidates: Sequence[dict],
    *,
    header: str,
    empty_note: str,
    include_score: bool,
) -> Rendered:
    """The keyword menu, with the evidence that put each row on it.

    ``matched_terms`` / ``matched_content`` are the load-bearing half. A score
    alone is not merely uninformative, it is misleading: under the per-character
    CJK tokenizer a semantically unrelated thread reaches a squashed 0.91 out of
    request-frame characters (帮/查/一/下) with zero topic-bearing overlap — and
    the judge runs precisely when the numeric gate found the candidates crowded.

    The header is rendered even when the list is empty. With nothing to match
    against, the model must be TOLD, not left to infer it from a missing section.
    """
    if not candidates:
        return Rendered(f"{header}\n\n{empty_note}")

    out = [f"{header}\n\n"]
    for i, candidate in enumerate(candidates):
        out.append(f"[Topic-{i}] {candidate['name']}\n")
        out.append(f"Description: {candidate['description']}\n")
        if include_score:
            out.append(f"Similarity score: {candidate['score']:.2f}\n")
        if candidate.get("matched_terms"):
            out.append(f"Matched terms: {', '.join(candidate['matched_terms'])}\n")
        if candidate.get("matched_content"):
            out.append(f"Matched content:\n{candidate['matched_content']}\n")
        elif candidate.get("raw_score", 0.0) > 0:
            # A candidate with a real BM25 score hit at least one query term, so
            # its evidence cannot legitimately be empty. This branch spent
            # 2026-04-15 → 2026-08-12 as the ONLY branch (the writer was deleted
            # while the reader lived in another file, and its `logger.debug`
            # sibling fired every turn for four months with nobody reading it);
            # if it fires now, the wiring broke again. Candidates at raw_score
            # 0.0 are the participant narratives merged in at a synthetic score
            # — they never went through BM25 and owe nothing, so they must not
            # trip this alarm (incident lesson #3: an alarm that cries wolf gets
            # silenced, and then it is gone).
            logger.warning(
                f"[NarrativeJudge] search candidate {i} "
                f"({candidate.get('id')}) scored {candidate.get('raw_score')} "
                f"but carries no BM25 evidence — rank_pool → candidate "
                f"assembly wiring is broken"
            )
        out.append("\n")
    return Rendered("".join(out))


# ── PARTICIPANT threads ────────────────────────────────────────────────────

PARTICIPANT_HEADER = "## Participant-Associated Topics (user is a PARTICIPANT):"


def render_participant_candidates(
    candidates: Sequence[dict],
    *,
    header: str = PARTICIPANT_HEADER,
) -> Rendered:
    """Threads the user was INVITED into — rendered first, by priority (P0-4).

    Renders EXACTLY what it is given; any cap belongs to the caller.
    """
    if not candidates:
        return Rendered("")

    out = [f"{header}\n\n"]
    for i, candidate in enumerate(candidates):
        out.append(f"[Participant-{i}] {candidate['name']}\n")
        out.append(f"Description: {candidate['description']}\n")
        out.append("\n")
    return Rendered("".join(out))
