"""
@file_name: merged_router.py
@date: 2026-08-26
@description: The merged-routing decider — one decision-model request that
answers "does this message continue the thread?" and "if not, where does it
go?" with one yes/no probability per candidate thread.

Why: the two-call path pays up to two serial helper-LLM calls per turn (prod
p50 8,924ms when both run). A decision model returns calibrated probabilities
in well under a second, and speed beats accuracy here — the agent loop's
`switch_narrative` / `create_narrative` tools catch a misfiled turn. On this
path the decision model is the ONLY decider: no LLM call, no LLM fallback.

Three units, each testable alone: `build_decision_request` (the questions),
`verdict_from_scores` (a pure threshold rule), `decide` (one call, never
raises). Where the turn lands is `merged_select`'s job, through the same
executors the two-call path uses.

The anchor question is asked whenever the anchor is continuable, whatever BM25
thinks of it: merging lets foreign threads into every decision, and on
continuity turns the anchor scores zero in 8.2%-49.3% of turns.

A failure is never a verdict: anything unusable returns `ok=False` and the
caller keeps the turn where it was. Reading a failure as "new topic" is the
D19 shape — the created thread becomes the anchor and gets rewritten to fit.
"""

from __future__ import annotations

import time as _perf
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple, TYPE_CHECKING

from loguru import logger

from narranexus.platform.agent_framework.llm.decision_model import (
    DecisionEndpoint,
    DecisionModelError,
    DecisionResult,
    YesNoQuestion,
    ask_yes_no,
    encode_questions,
    resolve_decision_endpoint,
    serialise,
)

from ..config import config
from . import routing_blocks
from .landings import candidate_labels

if TYPE_CHECKING:
    from ..models import Narrative


# ── the contract ───────────────────────────────────────────────────────────

#: The message belongs to the thread the conversation is already on. Kept
#: SEPARATE from `match` even when the anchor is also a menu row: the judge's
#: `search` exit cannot tell "confirmed the continuation" from "happened to pick
#: the anchor", and only the first of those must not be audited as a switch.
VERDICT_CONTINUE_ANCHOR = "continue_anchor"
#: A different existing thread from the keyword menu, by index.
VERDICT_MATCH = "match"
#: A thread the user was invited into, by index. Structural, exactly as today.
VERDICT_PARTICIPANT = "participant"
#: A real subject nothing on offer covers.
VERDICT_NEW = "new"
#: Nothing worth remembering as its own thread — the verdict, not a destination.
VERDICT_NO_TOPIC = "no_topic"

#: Written to `narrative_routing_audit.merged_verdict` when the call produced
#: nothing usable. A distinct value, not an empty string: "the model was asked
#: and could not be used" and "no model was asked" are different rows, and only
#: one of them is a provider problem.
VERDICT_FAILED = "failed"
#: Written on a TWO-CALL row (`merged_call = 0`) of a flag-on turn that never
#: reached the merged path: no decision credential qualified for the turn, or
#: resolving one raised. Without them such a row is identical to a flag-off
#: row and the merged path's coverage cannot be read from the table.
VERDICT_NO_CREDENTIAL = "no_credential"
VERDICT_CREDENTIAL_ERROR = "credential_error"


# ── candidate kinds ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CandidateKind:
    """One kind of switch target. Adding a kind = one row in CANDIDATE_KINDS."""

    verdict: str
    #: Question keys are ``f"{key_prefix}{index}"``; the index IS the index
    #: into the input list named by ``field`` — `resolve_choice` relies on it.
    key_prefix: str
    field: str
    #: Extra facts shown on every thread of this kind.
    flags: Mapping[str, bool]


#: Order is the tie-break priority: participants before menu rows (P0-4).
CANDIDATE_KINDS: Tuple[CandidateKind, ...] = (
    CandidateKind(VERDICT_PARTICIPANT, "p", "participants",
                  {"user_was_invited_into_this_thread": True}),
    CandidateKind(VERDICT_MATCH, "m", "menu", {}),
)
_KIND_BY_VERDICT = {kind.verdict: kind for kind in CANDIDATE_KINDS}

ANCHOR_KEY = "anchor"
NO_TOPIC_KEY = "no_topic"


# ── question text (generic only — binding rule #4) ─────────────────────────

THREAD_QUESTION = (
    "Does the current message belong to this thread, i.e. serve the same user "
    "goal?"
)
NO_TOPIC_QUESTION = (
    "Is the current message only a greeting, acknowledgement or venting with "
    "no subject worth remembering as its own thread?"
)
#: Shared by every thread question; scenario-specific routing belongs in the
#: agent's Awareness, never here.
ROUTING_RULES: Tuple[str, ...] = (
    "Threads are split by the user's GOAL, not by surface wording.",
    "A short acknowledgement or follow-up rides the thread it answers.",
    "A time gap alone never starts a new thread.",
    "Sub-tasks of one project stay in that project's thread.",
    "A one-off unrelated factual question is a new thread.",
    "A new task that merely reuses the same tool is a new thread.",
)


@dataclass(frozen=True)
class MergedRoutingInput:
    """Everything the merged decision reads.

    ``anchor_is_continuable`` is `anchor_rules.is_reusable_anchor`, applied by
    the caller so the merged path cannot disagree with the fast path about
    what counts as a thread.
    """

    query: str
    anchor: Optional["Narrative"]
    anchor_is_continuable: bool
    previous_query: str
    previous_response: str
    minutes_since_previous: Optional[float]
    #: Candidate dicts labelled by `landings.candidate_labels`, already
    #: deduplicated against the anchor (and the menu against participants).
    menu: List[dict] = field(default_factory=list)
    participants: List[dict] = field(default_factory=list)

    @property
    def offers_anchor(self) -> bool:
        return self.anchor is not None and self.anchor_is_continuable

    def candidates(self, kind: CandidateKind) -> List[dict]:
        return getattr(self, kind.field)


@dataclass(frozen=True)
class MergedRoutingRequest:
    """The one decision-model request, as sent."""

    state: Dict[str, Any]
    questions: Tuple[YesNoQuestion, ...]
    #: Field codes whose content hit its read-side cap, for `merged_truncated`.
    truncated: Tuple[str, ...]

    @property
    def input_chars(self) -> int:
        """Serialised state + questions, in the client's own encoding — the
        x axis of the latency model (`audit.merged_input_chars`)."""
        return len(
            serialise({"state": self.state, "questions": encode_questions(self.questions)})
        )


@dataclass(frozen=True)
class DecisionThresholds:
    stay: float
    switch: float
    no_topic: float

    @classmethod
    def from_config(cls) -> "DecisionThresholds":
        return cls(
            stay=config.NARRATIVE_DECISION_STAY_THRESHOLD,
            switch=config.NARRATIVE_DECISION_SWITCH_THRESHOLD,
            no_topic=config.NARRATIVE_DECISION_NO_TOPIC_THRESHOLD,
        )


@dataclass(frozen=True)
class ScoredVerdict:
    verdict: str
    match_index: int
    reason: str


@dataclass(frozen=True)
class MergedRoutingDecision:
    """One merged verdict, or the honest absence of one."""

    ok: bool
    verdict: str
    reason: str
    match_index: int
    elapsed_ms: int
    request: Optional[MergedRoutingRequest] = None
    #: The model's raw answer (scores, model, usage); None when no call returned.
    result: Optional[DecisionResult] = None


# ── question building ──────────────────────────────────────────────────────


def build_decision_request(inp: MergedRoutingInput) -> MergedRoutingRequest:
    """The state, and one question per candidate thread plus ``no_topic``.

    Question order: ``anchor`` (only when offered), then each CANDIDATE_KINDS
    row in order, then ``no_topic``. Read-side caps keep the head
    (`routing_blocks.clamp_head`) and every clip is reported.
    """
    truncated: List[str] = []

    def clamp(text: str, cap: int, code: str) -> str:
        clipped_text, clipped = routing_blocks.clamp_head(text or "", cap)
        if clipped:
            truncated.append(code)
        return clipped_text

    previous_query = clamp(inp.previous_query, config.MERGED_QUERY_MAX_CHARS, "prev_query")
    previous_response = clamp(
        inp.previous_response, config.MERGED_PREV_RESPONSE_MAX_CHARS, "prev_response"
    )
    previous_turn: Optional[Dict[str, Any]] = None
    if previous_query:
        previous_turn = {"user_query": previous_query, "agent_response": previous_response}
    elif previous_response:
        # The agent messaged first (a scheduled job): a short reply most
        # likely answers THAT message, so say so instead of an empty query.
        previous_turn = {
            "user_query": None,
            "agent_response": previous_response,
            "agent_messaged_proactively": True,
        }

    state: Dict[str, Any] = {
        "current_message": clamp(inp.query, config.MERGED_QUERY_MAX_CHARS, "query"),
        "previous_turn": previous_turn,
        "minutes_since_previous_turn": (
            round(inp.minutes_since_previous, 1)
            if inp.minutes_since_previous is not None
            else None
        ),
    }

    questions: List[YesNoQuestion] = []
    if inp.offers_anchor:
        name, summary = candidate_labels(inp.anchor)  # type: ignore[arg-type]
        questions.append(_thread_question(
            ANCHOR_KEY, {"name": name, "description": summary},
            {"is_thread_of_previous_turn": True},
        ))
    for kind in CANDIDATE_KINDS:
        for i, candidate in enumerate(inp.candidates(kind)):
            questions.append(_thread_question(f"{kind.key_prefix}{i}", candidate, kind.flags))
    questions.append(YesNoQuestion(key=NO_TOPIC_KEY, instructions=NO_TOPIC_QUESTION))

    return MergedRoutingRequest(
        state=state,
        questions=tuple(questions),
        truncated=tuple(dict.fromkeys(truncated)),
    )


def _thread_question(
    key: str, candidate: Mapping[str, Any], flags: Mapping[str, bool]
) -> YesNoQuestion:
    """Shows the fields `landings.candidate_labels` chose for every routing
    candidate, plus the BM25 evidence a menu row carries."""
    thread: Dict[str, Any] = {"name": candidate["name"], "summary": candidate["description"]}
    if candidate.get("matched_terms"):
        thread["matched_terms"] = list(candidate["matched_terms"])
    if candidate.get("matched_content"):
        thread["matched_excerpt"] = candidate["matched_content"]
    thread.update(flags)
    return YesNoQuestion(
        key=key,
        instructions={"question": THREAD_QUESTION, "rules": list(ROUTING_RULES), "thread": thread},
    )


# ── scores -> verdict ──────────────────────────────────────────────────────


def verdict_from_scores(
    scores: Mapping[str, float],
    *,
    offers_anchor: bool,
    candidate_counts: Mapping[str, int],
    thresholds: DecisionThresholds,
) -> ScoredVerdict:
    """The deterministic threshold rule. Pure; a missing score raises KeyError.

    ``candidate_counts`` maps a CANDIDATE_KINDS verdict to how many of that
    kind were asked about.

    1. best_other = the highest-scoring non-anchor candidate; ties go to the
       earlier kind (participants first, P0-4), then the lower index.
    2. best_other >= switch AND best_other > anchor -> that kind's verdict.
    3. elif anchor offered AND anchor >= stay -> continue_anchor.
    4. elif no_topic >= no_topic threshold -> no_topic.
    5. else -> new.
    """
    best: Optional[Tuple[CandidateKind, int, str, float]] = None
    for kind in CANDIDATE_KINDS:
        for i in range(candidate_counts.get(kind.verdict, 0)):
            key = f"{kind.key_prefix}{i}"
            # Strictly greater, so the earlier candidate keeps a tie.
            if best is None or scores[key] > best[3]:
                best = (kind, i, key, scores[key])

    anchor = scores[ANCHOR_KEY] if offers_anchor else None
    no_topic = scores[NO_TOPIC_KEY]
    anchor_text = f"anchor={anchor:.2f}" if anchor is not None else "no anchor"
    best_text = f"{best[2]}={best[3]:.2f}" if best else "no other thread"
    seen = f"{anchor_text}, {best_text}, no_topic={no_topic:.2f}"

    if best and best[3] >= thresholds.switch and (anchor is None or best[3] > anchor):
        return ScoredVerdict(best[0].verdict, best[1], f"decision model: switch — {seen}")
    if anchor is not None and anchor >= thresholds.stay:
        return ScoredVerdict(VERDICT_CONTINUE_ANCHOR, -1, f"decision model: stay — {seen}")
    if no_topic >= thresholds.no_topic:
        return ScoredVerdict(VERDICT_NO_TOPIC, -1, f"decision model: no topic — {seen}")
    return ScoredVerdict(VERDICT_NEW, -1, f"decision model: new — {seen}")


# ── the call ───────────────────────────────────────────────────────────────


def decision_endpoint_for_turn() -> Optional[DecisionEndpoint]:
    """This turn's decision credential, or None (the turn then takes the
    two-call path). Order: `NARRATIVE_DECISION_API_KEY`, else the agent
    owner's helper-slot provider key (installed by the turn's ingress) on the
    host of `NARRATIVE_DECISION_API_BASE` — the policy itself lives in
    `decision_model.resolve_decision_endpoint`."""
    return resolve_decision_endpoint(
        base_url=config.NARRATIVE_DECISION_API_BASE,
        platform_key=config.NARRATIVE_DECISION_API_KEY,
        model=config.NARRATIVE_DECISION_MODEL,
        timeout_seconds=config.NARRATIVE_DECISION_TIMEOUT_SECONDS,
    )


async def decide(
    inp: MergedRoutingInput, endpoint: DecisionEndpoint
) -> MergedRoutingDecision:
    """Ask once. Never raise, never guess.

    ``endpoint`` is passed explicitly, never read from process state, so
    concurrent turns of different users cannot use each other's key.

    A failure's ``reason`` is a fixed category only (it reaches
    `selection_reason`, which non-owners can see); the detail goes to the log.
    """
    def failed(category: str, elapsed_ms: int = 0, **kw: Any) -> MergedRoutingDecision:
        return MergedRoutingDecision(
            ok=False, verdict=VERDICT_FAILED, reason=f"decision model failure: {category}",
            match_index=-1, elapsed_ms=elapsed_ms, **kw,
        )

    try:
        request = build_decision_request(inp)
    except Exception as e:  # noqa: BLE001 — an assembly failure is not a verdict
        # Distinct log text from the call failure: "our bug" vs "provider
        # problem" must be tellable apart on merged_verdict='failed' rows.
        logger.warning(
            f"[MergedRouting] decision request assembly failed: "
            f"{type(e).__name__}: {e} (the turn stays where it was)"
        )
        return failed("assembly")

    # Timed after assembly: merged_ms is the call's own cost.
    started = _perf.monotonic()
    try:
        result = await ask_yes_no(endpoint, request.state, request.questions)
    except DecisionModelError as e:
        elapsed_ms = int((_perf.monotonic() - started) * 1000)
        # The client builds and scrubs this detail; it carries no key.
        logger.warning(
            f"[MergedRouting] decision call failed after {elapsed_ms}ms: "
            f"{e.category}: {e} (the turn stays where it was)"
        )
        return failed(e.category, elapsed_ms, request=request)
    except Exception as e:  # noqa: BLE001 — a client bug is not a verdict either
        elapsed_ms = int((_perf.monotonic() - started) * 1000)
        # Type only: an unexpected exception's text is not known to be key-free.
        logger.warning(
            f"[MergedRouting] decision call failed after {elapsed_ms}ms: "
            f"unexpected {type(e).__name__} (the turn stays where it was)"
        )
        return failed("unexpected", elapsed_ms, request=request)
    elapsed_ms = int((_perf.monotonic() - started) * 1000)

    try:
        answer = verdict_from_scores(
            result.scores,
            offers_anchor=inp.offers_anchor,
            candidate_counts={k.verdict: len(inp.candidates(k)) for k in CANDIDATE_KINDS},
            thresholds=DecisionThresholds.from_config(),
        )
        invalid = _contract_violation(answer, inp)
    except Exception as e:  # noqa: BLE001 — an unreadable answer is not a verdict
        invalid = f"scores unusable: {type(e).__name__}: {e}"
    if invalid:
        logger.warning(
            f"[MergedRouting] off-contract answer ({invalid}): "
            f"scores={result.scores} (the turn stays where it was)"
        )
        return failed("off_contract", elapsed_ms, request=request, result=result)

    # Raw per-question scores on the log line: thresholds can be re-tuned
    # offline without re-asking the model.
    logger.info(
        f"[MergedRouting] {answer.verdict}"
        f"{f' #{answer.match_index}' if answer.match_index >= 0 else ''} — "
        f"{answer.reason} | scores={result.scores}"
    )
    return MergedRoutingDecision(
        ok=True, verdict=answer.verdict, reason=answer.reason,
        match_index=answer.match_index, elapsed_ms=elapsed_ms,
        request=request, result=result,
    )


def allowed_verdicts(inp: MergedRoutingInput) -> FrozenSet[str]:
    """The verdicts THIS turn offers — the questions actually asked."""
    offered = {VERDICT_NEW, VERDICT_NO_TOPIC}
    if inp.offers_anchor:
        offered.add(VERDICT_CONTINUE_ANCHOR)
    offered.update(k.verdict for k in CANDIDATE_KINDS if inp.candidates(k))
    return frozenset(offered)


def _contract_violation(answer: ScoredVerdict, inp: MergedRoutingInput) -> Optional[str]:
    """Why this answer cannot be landed, or None. The threshold rule cannot
    produce these by construction; the landing still must never receive an
    index the input does not hold."""
    if answer.verdict not in allowed_verdicts(inp):
        return f"verdict '{answer.verdict}' was not offered on this turn"
    kind = _KIND_BY_VERDICT.get(answer.verdict)
    if kind and not 0 <= answer.match_index < len(inp.candidates(kind)):
        return f"{answer.verdict} index outside the asked candidates"
    return None


def resolve_choice(
    decision: MergedRoutingDecision, inp: MergedRoutingInput
) -> Optional[str]:
    """The narrative id a switch verdict names (bounds checked in `decide`)."""
    kind = _KIND_BY_VERDICT.get(decision.verdict)
    return inp.candidates(kind)[decision.match_index]["id"] if kind else None
