"""
@file_name: test_merged_routing.py
@date: 2026-08-26
@description: One routing decision per turn instead of two — the flow, the
zero-call shutter, the per-turn decision credential, the landings, and the
failure floor.

Merged routing runs BM25 FIRST on every turn, then either opens a zero-call
shutter (`evaluate_bypass`'s `anchor_match` verdict, moved ahead of
continuity) or asks a decision model one yes/no question per candidate thread
and turns the scores into continue_anchor / match(i) / participant(i) / new /
no_topic. No helper LLM runs on this path.

The decider changes; the executors do not: every landing is an existing code
path, and `test_downstream_cannot_tell_who_decided` pins that step_1 / step_4
never learn there was a change.

The failure floor (rule 6): a decision call that fails must NOT be read as
"new topic" (the D19 shape). Anchor present → stay on it, flagged
`merged_fallback_anchor`; no anchor → create, flagged `merged_fallback_new`.

The credential is resolved per turn (platform key, else the user's own key on
the decision host); a turn without one takes the two-call path.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone

import httpx
import pytest

from narranexus.platform.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    CliHelperConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)
from narranexus.platform.agent_framework.llm import decision_model, helper_sdk
from narranexus.platform.agent_framework.llm.decision_model import (
    DecisionModelError,
    DecisionResult,
    encode_questions,
    serialise,
)
from narranexus.platform.narrative._narrative_impl import merged_router
from narranexus.platform.narrative._narrative_impl.routing_gate import (
    GateDecision,
    evaluate_bypass,
    pick_menu,
    shutter_opens,
)
from narranexus.platform.narrative.config import config
from narranexus.platform.narrative.models import (
    ConversationSession,
    NarrativeSearchResult,
)
from narranexus.platform.narrative.narrative_service import NarrativeService
from narranexus.platform.repository.narrative_routing_audit_repository import (
    NarrativeRoutingAuditRepository,
)

pytestmark = pytest.mark.asyncio

AGENT = "agent_merged_flow"
USER = "user_merged_flow"

# Scores the anchor decisively and nothing else (the others are restaurant
# threads) — a lone scoring candidate, i.e. the shutter's happy path.
ANCHOR_QUERY = "部署脚本报错的日志怎么看"
# Scores the restaurant thread and not the anchor: the merged call has a real
# menu row to be tempted by, which is the whole risk §3.2 is about.
FOREIGN_QUERY = "餐厅推荐一下"


PLATFORM_KEY = "test-platform-key"


@pytest.fixture
def merged_on(monkeypatch):
    monkeypatch.setattr(config, "NARRATIVE_MERGED_ROUTING_ENABLED", True)
    monkeypatch.setattr(config, "NARRATIVE_DECISION_API_KEY", PLATFORM_KEY)


@pytest.fixture
def service(db_client, monkeypatch):
    svc = NarrativeService(agent_id=AGENT, database_client=db_client)
    svc._crud.set_database_client(db_client)
    svc._retrieval.set_database_client(db_client)

    async def _get():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _get)

    # The two tiers merged routing replaces, and the helper-LLM factory itself
    # (every `get_helper_sdk()` resolves through this registry call). Any of
    # them running on a merged turn is a bug, not a fallback.
    def _no_continuity():
        raise AssertionError("the continuity tier must not run on a merged turn")

    async def _no_judge(**kw):
        raise AssertionError("the judge must not run on a merged turn")

    def _no_helper_llm(*a, **kw):
        raise AssertionError("no helper LLM may run on a merged turn")

    monkeypatch.setattr(svc, "_get_continuity_detector", _no_continuity)
    monkeypatch.setattr(svc._retrieval, "_llm_judge_unified", _no_judge)
    monkeypatch.setattr(helper_sdk, "llm_client_registry", _no_helper_llm)
    return svc


class _Decider:
    """The decision-model edge — the only thing stubbed. Every asked key
    scores 0.0 unless named in ``scores``. Records every call."""

    def __init__(self, scores: dict | None = None, *, boom: Exception | None = None,
                 delay: float = 0.0):
        self.scores = scores or {}
        self.boom = boom
        self.delay = delay
        self.calls: list[dict] = []

    async def __call__(self, endpoint, state, questions):
        self.calls.append({"endpoint": endpoint, "state": state, "questions": questions})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.boom is not None:
            raise self.boom
        asked = {q.key: 0.0 for q in questions}
        asked.update({k: v for k, v in self.scores.items() if k in asked})
        return DecisionResult(asked, "typesafe/jev-1.13", 100, 10, 1)

    def keys(self, call: int = 0) -> list[str]:
        return [q.key for q in self.calls[call]["questions"]]


def _decider(monkeypatch, scores: dict | None = None, **kw) -> _Decider:
    decider = _Decider(scores, **kw)
    monkeypatch.setattr(merged_router, "ask_yes_no", decider)
    return decider


def _no_decider(monkeypatch) -> None:
    async def _boom(*a, **kw):
        raise AssertionError("the decision model must not be asked")

    monkeypatch.setattr(merged_router, "ask_yes_no", _boom)


STAY = {"anchor": 0.9}


def _session(anchor: str | None):
    now = datetime.now(timezone.utc)
    return ConversationSession(
        session_id="sess_merged", user_id=USER, agent_id=AGENT,
        created_at=now, last_query_time=now,
        last_query="部署脚本报错怎么修", last_response="先看日志的最后 20 行",
        current_narrative_id=anchor,
    )


async def _seed(service, *, twin: bool = False):
    """An anchor thread plus foreign competition (and optionally a near-twin)."""
    anchor = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="部署脚本报错排查", description="",
    )
    foreign = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="纽约餐厅推荐", description="",
    )
    if twin:
        await service.create_narrative(
            agent_id=AGENT, user_id=USER, title="部署脚本报错复盘", description="",
        )
    return anchor, foreign


async def _row(db_client):
    rows = await NarrativeRoutingAuditRepository(db_client).recent(agent_id=AGENT)
    assert rows, "no audit row was written"
    return rows[0]


# ===================================================================== #
# The switch                                                            #
# ===================================================================== #


def test_the_switch_ships_off():
    assert config.NARRATIVE_MERGED_ROUTING_ENABLED is False


async def test_with_the_switch_off_the_two_call_path_is_untouched(
    service, db_client, monkeypatch
):
    """A flag whose OFF state is not pinned is a flag that has already changed
    behaviour. Nothing on the merged path may even be constructed."""
    anchor, _ = await _seed(service)

    def _boom(*a, **kw):
        raise AssertionError("merged routing ran with its switch off")

    monkeypatch.setattr(merged_router, "decision_endpoint_for_turn", _boom)
    monkeypatch.setattr(merged_router, "build_decision_request", _boom)

    class _Detector:
        async def detect(self, **kw):
            from narranexus.platform.narrative.models import ContinuityResult

            return ContinuityResult(is_continuous=True, confidence=0.9, reason="stub")

    monkeypatch.setattr(service, "_get_continuity_detector", lambda: _Detector())

    result = await service.select(
        AGENT, USER, ANCHOR_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "continuous"
    row = await _row(db_client)
    assert row["continuity_ran"] == 1
    assert not row["merged_call"], "the merged columns must stay empty"
    assert row["merged_verdict"] in (None, "")
    assert row["merged_ms"] is None


def test_both_switches_on_is_an_untested_world_and_refuses_to_boot():
    """Bucket governance OFF is what makes a default bucket un-continuable, and
    the merged path's anchor question is built on that. Both flags on is a
    combination nothing has ever measured, so it fails at startup rather than
    in a routing decision (`config` raises at import)."""
    probe = subprocess.run(
        [sys.executable, "-c", "import narranexus.platform.narrative.config"],
        capture_output=True, text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": ":".join(sys.path),
            "NARRATIVE_DEFAULT_BUCKETS_ENABLED": "1",
            "NARRATIVE_MERGED_ROUTING_ENABLED": "1",
        },
    )

    assert probe.returncode != 0, "both switches on must not boot"
    assert "NARRATIVE_MERGED_ROUTING_ENABLED" in probe.stderr


def _boot(**env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import narranexus.platform.narrative.config"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ":".join(sys.path), **env},
    )


def test_the_flag_on_without_a_platform_key_boots():
    """Per-user keys make an empty platform key a legal deployment: turns
    without a qualifying credential take the two-call path."""
    probe = _boot(NARRATIVE_MERGED_ROUTING_ENABLED="1", NARRATIVE_DECISION_API_KEY="")
    assert probe.returncode == 0, probe.stderr


@pytest.mark.parametrize(
    "name,value",
    [
        ("NARRATIVE_DECISION_STAY_THRESHOLD", "1.5"),
        ("NARRATIVE_DECISION_SWITCH_THRESHOLD", "-0.1"),
        ("NARRATIVE_DECISION_NO_TOPIC_THRESHOLD", "2"),
        ("NARRATIVE_DECISION_TIMEOUT_SECONDS", "0"),
        ("NARRATIVE_DECISION_API_BASE", "not-a-url"),
        ("NARRATIVE_DECISION_API_KEY", "sk-bad key"),
        ("NARRATIVE_DECISION_API_KEY", "sk-badé"),
    ],
)
def test_the_flag_on_with_unusable_decision_settings_refuses_to_boot(name, value):
    probe = _boot(NARRATIVE_MERGED_ROUTING_ENABLED="1", **{name: value})
    assert probe.returncode != 0
    assert name in probe.stderr


def test_the_flag_off_places_no_requirement_on_decision_settings():
    probe = _boot(
        NARRATIVE_MERGED_ROUTING_ENABLED="0",
        NARRATIVE_DECISION_STAY_THRESHOLD="1.5",
        NARRATIVE_DECISION_API_BASE="not-a-url",
    )
    assert probe.returncode == 0, probe.stderr


# ===================================================================== #
# Rule 5 · the menu: deduplicated, scored rows only, capped             #
# ===================================================================== #


def test_rule5_the_anchor_is_removed_from_the_menu():
    """Asked about twice, a "stay" could be audited as a switch."""
    ranked = [
        NarrativeSearchResult(narrative_id="nar_anchor", similarity_score=0.9,
                              rank=1, raw_score=9.0),
        NarrativeSearchResult(narrative_id="nar_other", similarity_score=0.5,
                              rank=2, raw_score=1.0),
    ]

    menu = pick_menu(ranked, exclude_ids={"nar_anchor"}, limit=3)

    assert [r.narrative_id for r in menu] == ["nar_other"]


def test_rule5_a_participant_thread_is_excluded_from_the_menu_too():
    """It already has its own question with its own priority."""
    ranked = [
        NarrativeSearchResult(narrative_id="nar_task", similarity_score=0.9,
                              rank=1, raw_score=9.0),
        NarrativeSearchResult(narrative_id="nar_other", similarity_score=0.5,
                              rank=2, raw_score=1.0),
    ]

    menu = pick_menu(ranked, exclude_ids={"nar_task"}, limit=3)

    assert [r.narrative_id for r in menu] == ["nar_other"]


def test_rule5_a_zero_scoring_candidate_never_reaches_the_menu():
    """Zero BM25 score means zero term overlap: no evidence for switching."""
    ranked = [
        NarrativeSearchResult(narrative_id="nar_a", similarity_score=0.5,
                              rank=1, raw_score=2.0),
        NarrativeSearchResult(narrative_id="nar_zero", similarity_score=0.0,
                              rank=2, raw_score=0.0),
    ]

    menu = pick_menu(ranked, exclude_ids=set(), limit=3)

    assert [r.narrative_id for r in menu] == ["nar_a"]


def test_rule5_the_menu_is_capped_at_the_configured_size():
    ranked = [
        NarrativeSearchResult(narrative_id=f"nar_{i}", similarity_score=0.5,
                              rank=i + 1, raw_score=9.0 - i)
        for i in range(6)
    ]

    menu = pick_menu(ranked, exclude_ids=set(), limit=3)

    assert [r.narrative_id for r in menu] == ["nar_0", "nar_1", "nar_2"]


# ===================================================================== #
# Rule 3 · the shutter reads floor/margin + identity, never the total   #
# ===================================================================== #


def test_rule3_a_huge_but_crowded_score_does_not_open_the_shutter():
    """Real prod row (audit id=1): top1 86.54, top2 71.71, margin 1.21. Every
    DIVERTED misconfirmation in the four-arm dry run sat at margin 1.06-1.27 —
    an enormous absolute score that means the anchor merely EDGED OUT a rival."""
    gate = GateDecision(
        short_circuit=False, reason="crowded", top1_raw=86.54, top2_raw=71.71,
        margin=1.21,
    )

    bypass = evaluate_bypass(
        gate, top1_narrative_id="nar_anchor", anchor_narrative_id="nar_anchor",
        is_user_chat=True, has_participant_narratives=False,
    )

    assert not shutter_opens(bypass)
    assert bypass.reason == "score_gate"


def test_rule3_a_modest_but_clear_score_does_open_it():
    gate = GateDecision(
        short_circuit=True, reason="clear", top1_raw=3.5, top2_raw=1.0, margin=3.5,
    )

    bypass = evaluate_bypass(
        gate, top1_narrative_id="nar_anchor", anchor_narrative_id="nar_anchor",
        is_user_chat=True, has_participant_narratives=False,
    )

    assert shutter_opens(bypass)
    assert bypass.reason == "anchor_match"


def test_rule3_the_shutter_is_exactly_the_anchor_match_verdict():
    """Not a new rule: the shutter IS `evaluate_bypass` moved ahead of
    continuity. Every other verdict — including the ones that GRANT a bypass on
    the two-call path — leaves the shutter shut, because only `anchor_match`
    carries both halves (floor+margin cleared AND the turn stays put)."""
    gate = GateDecision(
        short_circuit=True, reason="clear", top1_raw=9.0, top2_raw=1.0, margin=9.0,
    )

    background = evaluate_bypass(
        gate, top1_narrative_id="nar_other", anchor_narrative_id=None,
        is_user_chat=False, has_participant_narratives=False,
    )
    assert background.granted and background.reason == "background_scope"
    assert not shutter_opens(background)

    switch = evaluate_bypass(
        gate, top1_narrative_id="nar_other", anchor_narrative_id="nar_anchor",
        is_user_chat=True, has_participant_narratives=False,
    )
    assert not shutter_opens(switch) and switch.reason == "anchor_miss"

    participant = evaluate_bypass(
        gate, top1_narrative_id="nar_anchor", anchor_narrative_id="nar_anchor",
        is_user_chat=True, has_participant_narratives=True,
    )
    assert not shutter_opens(participant)
    assert participant.reason == "participant_present"


async def test_the_shutter_confirms_the_thread_with_zero_calls(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    _no_decider(monkeypatch)

    result = await service.select(
        AGENT, USER, ANCHOR_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "anchor_confirmed"
    assert [n.id for n in result.narratives] == [anchor.id]
    row = await _row(db_client)
    assert row["merged_call"] == 1
    assert row["merged_verdict"] in (None, ""), "no model was asked"
    assert row["merged_ms"] is None, "a tier that did not run costs NULL, not 0"
    assert row["bypass_reason"] == "anchor_match"
    assert row["gate_short_circuit"] == 1
    assert row["continuity_ms"] is None and row["judge_ms"] is None


async def test_a_crowded_pool_pays_for_exactly_one_call(
    service, db_client, merged_on, monkeypatch
):
    """The whole point: the turn that used to cost continuity + judge now costs
    one decision call. `service` makes both old tiers and the helper-LLM
    factory land mines, so reaching this row at all proves none of them ran."""
    anchor, _ = await _seed(service, twin=True)
    decider = _decider(monkeypatch, STAY)

    result = await service.select(
        AGENT, USER, "部署脚本报错怎么修", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert len(decider.calls) == 1
    assert result.selection_method == "merged_continue"
    assert [n.id for n in result.narratives] == [anchor.id]


# ===================================================================== #
# Rule 1 (service level) · the anchor is asked about whatever BM25       #
# thinks of it                                                          #
# ===================================================================== #


async def test_rule1_the_anchor_is_asked_about_even_scoring_zero(
    service, db_client, merged_on, monkeypatch
):
    """The p07 shape: the menu's top row is a foreign thread and the anchor is
    nowhere in it. On the two-call path this turn never built a menu at all."""
    anchor, foreign = await _seed(service)
    decider = _decider(monkeypatch, STAY)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    asked = {q.key: q.instructions for q in decider.calls[0]["questions"]}
    assert asked["anchor"]["thread"]["name"] == "部署脚本报错排查", (
        "the anchor question was dropped for scoring zero"
    )
    assert asked["m0"]["thread"]["name"] == "纽约餐厅推荐"
    row = await _row(db_client)
    assert row["anchor_in_menu"] == 0, (
        "the production-side instrument for §3.2 — how often the unconditional "
        "injection is the only reason the anchor is there at all"
    )
    assert row["anchor_bm25_rank"] is None, "zero score has no rank"


async def test_the_anchors_bm25_standing_is_recorded_when_it_does_score(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service, twin=True)
    _decider(monkeypatch, STAY)

    await service.select(
        AGENT, USER, "部署脚本报错怎么修", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["anchor_bm25_rank"] >= 1
    assert row["anchor_raw_score"] > 0
    assert row["anchor_in_menu"] == 1


# ===================================================================== #
# The four landings — decider changes, executors do not                 #
# ===================================================================== #


async def test_match_lands_on_the_menu_row_the_model_named(
    service, db_client, merged_on, monkeypatch
):
    anchor, foreign = await _seed(service)
    _decider(monkeypatch, {"m0": 0.9})

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_match"
    assert result.narratives[0].id == foreign.id
    assert result.is_new is False
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_MATCH
    assert row["chosen_narrative_id"] == foreign.id


async def test_new_goes_through_the_existing_creator(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    _decider(monkeypatch)

    result = await service.select(
        AGENT, USER, "帮我订下周去东京的机票", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_new"
    assert result.is_new is True
    created = result.narratives[0]
    assert created.id not in (anchor.id,)
    assert created.topic_keywords, (
        "created through `create_from_query`, so it carries the same BM25 "
        "routing surface as a thread born on the two-call path"
    )


async def test_no_topic_hands_the_turn_to_the_existing_landing_rule(
    service, db_client, merged_on, monkeypatch
):
    """`_land_no_topic_turn` is untouched: anchor-first, and the anchor's
    retrieval surface is NOT rewritten by a contentless turn."""
    anchor, _ = await _seed(service)
    _decider(monkeypatch, {"no_topic": 0.9})

    result = await service.select(
        AGENT, USER, "哈哈哈", session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "no_topic_anchored"
    assert result.no_durable_topic is True
    assert [n.id for n in result.narratives] == [anchor.id]
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_NO_TOPIC


async def test_no_topic_with_no_anchor_still_keeps_the_turn_in_history(
    service, db_client, merged_on, monkeypatch
):
    await _seed(service)
    _decider(monkeypatch, {"no_topic": 0.9})

    result = await service.select(
        AGENT, USER, "哈哈哈", session=_session(None),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "new_created"
    assert result.no_durable_topic is True


async def test_participant_priority_survives_the_merge(
    service, db_client, merged_on, monkeypatch
):
    """P0-4 stays structural on both ends: the shutter cannot open while a
    participant thread exists, and the model picks one through its own verdict
    rather than out of the keyword menu."""
    anchor, _ = await _seed(service)
    invited = await service.create_narrative(
        agent_id=AGENT, user_id="someone_else", title="客户 A 的报价单", description="",
    )
    monkeypatch.setattr(
        service._retrieval, "_get_participant_narratives",
        lambda **kw: _immediately([invited]),
    )
    decider = _decider(monkeypatch, {"p0": 0.9})

    result = await service.select(
        AGENT, USER, ANCHOR_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert len(decider.calls) == 1, "a participant thread must deny the shutter"
    assert result.selection_method == "merged_participant"
    assert result.narratives[0].id == invited.id
    row = await _row(db_client)
    assert row["bypass_reason"] == "participant_present"


async def _immediately(value):
    return value


# ===================================================================== #
# Rule 6 · the failure floor                                            #
# ===================================================================== #


async def test_rule6_a_failed_call_stays_on_the_anchor(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    _decider(monkeypatch, boom=DecisionModelError("timeout", "decision model timed out after 3.0s"))

    creator_calls: list = []
    original = service._retrieval.create_from_query

    async def _spy(**kw):
        creator_calls.append(kw)
        return await original(**kw)

    monkeypatch.setattr(service._retrieval, "create_from_query", _spy)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_anchor"
    assert [n.id for n in result.narratives] == [anchor.id]
    assert result.is_new is False
    assert creator_calls == [], (
        "D19: a failure in the deciding tier must never fall through to "
        "creation while there is a thread to stay on"
    )
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_FAILED
    assert row["merged_ms"] is not None, "the failed call still cost time"


@pytest.mark.parametrize(
    "error",
    [
        DecisionModelError("http_429", "HTTP 429: Rate limit exceeded sk-from-server"),
        DecisionModelError("http_500", "HTTP 500: Internal Server Error sk-from-server"),
        DecisionModelError("http_403", "HTTP 403: invalid token sk-from-server"),
        DecisionModelError("transport", "LocalProtocolError sk-from-server"),
        DecisionModelError("timeout", "timed out sk-from-server"),
        RuntimeError("an unexpected client bug sk-from-server"),
    ],
    ids=["429", "500", "403", "transport", "timeout", "unexpected"],
)
async def test_rule6_any_call_failure_holds_the_anchor_never_the_llm_path(
    service, db_client, merged_on, monkeypatch, error
):
    """Once a credential resolved and the merged path was chosen, a failed call
    lands on the failure floor — never on the two-call LLM path (the
    `service` fixture's land mines would fire) and never on `new`."""
    anchor, _ = await _seed(service)
    _decider(monkeypatch, boom=error)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_anchor"
    assert [n.id for n in result.narratives] == [anchor.id]
    category = getattr(error, "category", "unexpected")
    assert result.selection_reason.endswith(f"decision model failure: {category}")
    assert "sk-from-server" not in result.selection_reason, (
        "selection_reason is shown to non-owners: categories only"
    )


async def test_rule6_scores_missing_a_question_are_a_failure_not_a_guess(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)

    async def _partial(endpoint, state, questions):
        return DecisionResult({"no_topic": 0.1}, "m", 0, 0, 1)

    monkeypatch.setattr(merged_router, "ask_yes_no", _partial)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_anchor"
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_FAILED


async def test_rule6_with_no_anchor_a_failure_may_create_but_must_say_so(
    service, db_client, merged_on, monkeypatch
):
    """Running bare would drop a first-contact turn out of the user's own chat
    history (history endpoints are narrative-scoped), so creating is right —
    what is forbidden is doing it SILENTLY, indistinguishable from a real
    "new topic" verdict."""
    await _seed(service)
    _decider(monkeypatch, boom=DecisionModelError("http_500", "decision model returned HTTP 500"))

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(None),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_new"
    assert result.is_new is True
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_FAILED


async def test_rule6_a_bucket_anchor_is_not_a_thread_to_fall_back_onto(
    service, db_client, merged_on, monkeypatch
):
    """`is_reusable_anchor` is THE definition, and the fallback consumes it too
    — otherwise a failed call would re-pin the turn to a container whose
    retrieval surface never updates (C-1, 26.4% of prod user turns)."""
    bucket = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="GreetingAndCourtesy", description="",
    )
    bucket.is_special = "default"
    await service.save_narrative_to_db(bucket)
    _decider(monkeypatch, boom=DecisionModelError("transport", "boom"))

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(bucket.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_new"
    assert result.narratives[0].id != bucket.id


async def test_a_bucket_anchor_is_never_asked_about_so_cannot_be_continued(
    service, db_client, merged_on, monkeypatch
):
    """A legacy container is a verdict about an earlier turn, not a thread: no
    anchor question is asked, so even a model eager to "stay" cannot re-pin
    the turn to it."""
    bucket = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="GreetingAndCourtesy", description="",
    )
    bucket.is_special = "default"
    await service.save_narrative_to_db(bucket)
    decider = _decider(monkeypatch, STAY)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(bucket.id),
        trigger="chat", is_user_chat=True,
    )

    assert "anchor" not in decider.keys()
    assert all(n.id != bucket.id for n in result.narratives)


async def test_the_offered_verdicts_are_derived_from_the_input():
    """One derivation of "which answers exist on this turn", read by the
    contract check."""
    base = dict(
        query="q", previous_query="", previous_response="",
        minutes_since_previous=None, menu=[], participants=[],
    )
    bare = merged_router.MergedRoutingInput(
        anchor=None, anchor_is_continuable=False, **base
    )
    # An empty menu withdraws `match` too (round 6, I3): no index to give.
    assert merged_router.allowed_verdicts(bare) == frozenset(
        {merged_router.VERDICT_NEW, merged_router.VERDICT_NO_TOPIC}
    )
    with_menu = merged_router.MergedRoutingInput(
        anchor=None, anchor_is_continuable=False,
        **{**base, "menu": [
            {"id": "m", "type": "search", "name": "n", "description": "d"}
        ]},
    )
    assert merged_router.VERDICT_MATCH in merged_router.allowed_verdicts(
        with_menu
    )
    with_part = merged_router.MergedRoutingInput(
        anchor=None, anchor_is_continuable=False,
        **{**base, "participants": [
            {"id": "p", "type": "participant", "name": "n", "description": "d"}
        ]},
    )
    assert merged_router.VERDICT_PARTICIPANT in merged_router.allowed_verdicts(
        with_part
    )


async def test_only_the_first_participants_are_asked_about_and_the_cut_is_flagged(
    service, db_client, merged_on, monkeypatch
):
    """10 invitations, cap 8: exactly p0..p7 get a question (prefix — the ORDER
    is the P0-4 priority), so no verdict can name a thread off the ballot, and
    the cut surfaces in merged_truncated."""
    from narranexus.platform.narrative._narrative_impl import merged_prep

    anchor, _ = await _seed(service)
    ten = []
    for i in range(10):
        n = await service.create_narrative(
            agent_id=AGENT, user_id=f"user_owner_{i}",
            title=f"Invited task {i}", description="",
        )
        ten.append(n)

    real_prepare = merged_prep.prepare_merged_routing

    async def _prepare_with_ten(retrieval, query, user_id, agent_id, **kw):
        prep = await real_prepare(retrieval, query, user_id, agent_id, **kw)
        object.__setattr__(prep.scored, "participant_narratives", ten)
        return prep

    monkeypatch.setattr(
        "narranexus.platform.narrative._narrative_impl.merged_select."
        "prepare_merged_routing",
        _prepare_with_ten,
    )
    decider = _decider(monkeypatch, {"p7": 0.9})

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    participant_keys = [k for k in decider.keys() if k.startswith("p")]
    assert participant_keys == [f"p{i}" for i in range(8)]
    assert result.selection_method == "merged_participant"
    assert result.narratives[0].id == ten[7].id
    row = await _row(db_client)
    assert "participants" in (row["merged_truncated"] or "")


async def test_the_menu_knob_does_not_shrink_the_match_landing(
    service, db_client, merged_on, monkeypatch
):
    """Review round 3, I2: MERGED_MENU_SIZE is an env knob that caps the
    BALLOT; the landing's trailing context must answer to
    MAX_NARRATIVES_IN_CONTEXT alone. With the menu squeezed to 1, a match
    verdict must still hand the ChatModule the same number of threads the
    two-call path would."""
    anchor, _foreign = await _seed(service)
    # A second thread sharing the foreign query's subject, so the full
    # ranking holds two scored non-anchor rows while the ballot holds one.
    await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="纽约餐厅预订记录", description="",
    )
    monkeypatch.setattr(config, "MERGED_MENU_SIZE", 1)
    _decider(monkeypatch, {"m0": 0.9})

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_match"
    assert len(result.narratives) >= 2, (
        "the landing shrank with the menu knob — trailing context must come "
        "from the full ranking, not the ballot"
    )
    assert all(n.id != anchor.id for n in result.narratives)


async def test_rule6_a_request_assembly_failure_is_a_failure_not_a_dead_turn(
    service, db_client, merged_on, monkeypatch
):
    """Rule 6 covers assembly, not just the call: un-caught, a bad candidate
    field would kill the user's whole turn."""
    anchor, _ = await _seed(service)
    _decider(monkeypatch, STAY)

    def _boom(inp):
        raise KeyError("a candidate field went missing")

    monkeypatch.setattr(merged_router, "build_decision_request", _boom)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_fallback_anchor"
    assert [n.id for n in result.narratives] == [anchor.id]
    row = await _row(db_client)
    assert row["merged_verdict"] == merged_router.VERDICT_FAILED
    assert row["merged_input_chars"] is None  # no request was built


async def test_a_menu_row_from_beyond_the_snippet_head_still_carries_evidence(
    service, db_client
):
    """Review round 6, I2: pick_menu selects AFTER excluding the anchor and
    scoring participants, so a menu row can come from past the snippet head
    window with matched_snippet="". The snippet is the load-bearing half of
    the evidence (terms alone mislead — the CJK frame-word collision), and a
    bare row also trips the wiring-broken alarm. build_menu_candidates must
    backfill it."""
    from narranexus.platform.narrative.models import NarrativeSearchResult
    from narranexus.platform.narrative._narrative_impl.landings import (
        build_menu_candidates,
    )

    thread = await service.create_narrative(
        agent_id=AGENT, user_id=USER, title="部署脚本报错排查", description="",
    )
    bare_row = NarrativeSearchResult(
        narrative_id=thread.id, similarity_score=0.9, rank=7,
        raw_score=9.0, matched_terms=["部署"], matched_snippet="",
    )

    candidates = await build_menu_candidates(service._crud, [bare_row])

    assert candidates[0]["matched_content"], (
        "a scored menu row reached the model without its evidence — the "
        "snippet must be backfilled for rows beyond the head window"
    )
    assert "部署" in candidates[0]["matched_content"]


async def test_an_anchor_that_is_also_a_participant_is_not_on_the_ballot_twice(
    service, db_client, merged_on, monkeypatch
):
    """Review round 9, I1: after a participant landing, the landed thread is
    the next turn's anchor AND still in the participant list. Unfiltered, it
    rendered in two sections, two verdicts pointed at it, and a "stay" was
    audited as a "switch" (merged_participant) — polluting the very columns
    the flag exists to read. The participant SECTION must exclude the anchor;
    participant index 0 must resolve to the OTHER invitation."""
    anchor, _ = await _seed(service)
    other = await service.create_narrative(
        agent_id=AGENT, user_id="user_owner_x", title="Invited task", description="",
    )

    async def _participants(self, *, user_id, agent_id):
        return [anchor, other]

    from narranexus.platform.narrative._narrative_impl.retrieval import (
        NarrativeRetrieval,
    )
    monkeypatch.setattr(
        NarrativeRetrieval, "_get_participant_narratives", _participants
    )
    _decider(monkeypatch, {"p0": 0.9})

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "merged_participant"
    assert result.narratives[0].id == other.id, (
        "participant index 0 resolved to the anchor — it was on the ballot "
        "twice, and a stay would be audited as a switch"
    )


# ===================================================================== #
# Instruments and contracts                                             #
# ===================================================================== #


async def test_the_merged_row_records_what_it_cost_and_what_it_read(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    decider = _decider(monkeypatch, STAY)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    call = decider.calls[0]
    assert row["merged_call"] == 1
    assert row["merged_verdict"] == merged_router.VERDICT_CONTINUE_ANCHOR
    assert row["merged_ms"] is not None
    assert row["merged_input_chars"] == len(serialise(
        {"state": call["state"], "questions": encode_questions(call["questions"])}
    )), "the latency model's x-axis counts the state AND every question"
    assert row["merged_truncated"] == ""
    assert row["candidates"], "the pool that produced the menu is on the row"
    assert row["bypass_score_gate"] is not None, (
        "the floor/margin series must keep accumulating independently — the day "
        "a new rule ships is the day the old distribution stops, unless it is "
        "recorded separately (R3.3)"
    )


async def test_retrieve_ms_on_a_merged_row_is_the_bm25_pass_alone(
    service, db_client, merged_on, monkeypatch
):
    """Third population, same column. On the two-call path `retrieve_ms`
    CONTAINS `judge_ms` — an ambiguity that already sent two readers to the same
    wrong conclusion ("routing spends 6 seconds in the database"). On a merged
    row the decision call has its own column, so retrieve_ms must be the
    scoring pass only: a slow model may not leak into it."""
    anchor, _ = await _seed(service)
    _decider(monkeypatch, STAY, delay=0.25)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert row["merged_ms"] >= 250
    assert row["retrieve_ms"] < row["merged_ms"], (
        "the merged call's latency leaked into the retrieval tier's column"
    )


async def test_a_truncated_request_field_is_flagged_never_silent(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    _decider(monkeypatch, STAY)
    session = _session(anchor.id)
    session.last_response = "回复开头" + ("x" * 9000)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=session,
        trigger="chat", is_user_chat=True,
    )

    row = await _row(db_client)
    assert "prev_response" in (row["merged_truncated"] or "")


async def test_downstream_cannot_tell_who_decided(
    service, db_client, merged_on, monkeypatch
):
    """step_1 / step_4 read `narratives` / `is_new` / `no_durable_topic` /
    `retrieval_method` and never branch on `selection_method`. The merged path
    fills the same contract, so nothing downstream needs to know."""
    anchor, _ = await _seed(service)
    _decider(monkeypatch, STAY)
    session = _session(anchor.id)

    result = await service.select(
        AGENT, USER, FOREIGN_QUERY, session=session,
        trigger="chat", is_user_chat=True,
    )

    assert result.narratives and result.selection_reason
    assert result.retrieval_method
    assert result.is_new is False
    assert result.no_durable_topic is False
    # The observability surface is downstream too (round 8, I2): select()
    # leaves these empty at its own exit, so the merged arm must match —
    # a panel that shows scores on one arm only outs the decider.
    assert result.best_score is None
    assert result.scores == {}
    assert session.current_narrative_id == anchor.id, (
        "the session anchor advances by the same rule on both paths"
    )


async def test_a_background_turn_never_moves_the_anchor_on_the_merged_path_either(
    service, db_client, merged_on, monkeypatch
):
    anchor, foreign = await _seed(service)
    _decider(monkeypatch, {"m0": 0.9})
    session = _session(anchor.id)

    await service.select(
        AGENT, USER, FOREIGN_QUERY, session=session,
        trigger="job", is_user_chat=False,
    )

    assert session.current_narrative_id == anchor.id, (
        "background traffic must leave the next user message's continuity "
        "anchored to the last real exchange"
    )


async def test_the_new_columns_are_registered_on_both_dialects():
    from narranexus.platform.utils.db.schema_registry import TABLES

    columns = {c.name: c for c in TABLES["narrative_routing_audit"].columns}
    for name in (
        "merged_call", "merged_verdict", "merged_ms", "merged_input_chars",
        "merged_truncated", "anchor_bm25_rank", "anchor_raw_score",
        "anchor_in_menu",
    ):
        col = columns.get(name)
        assert col is not None, f"narrative_routing_audit.{name} not registered"
        assert col.sqlite_type and col.mysql_type
        assert col.nullable, (
            "prod rows predate this column; NOT NULL would fail the ALTER on a "
            "live table (binding rule #6)"
        )


# ===================================================================== #
# The decision credential is resolved per turn                          #
# ===================================================================== #

DECISION_HOST_BASE = "https://api.netmind.ai/inference-api/openai/v1"


@pytest.fixture
def merged_on_no_platform_key(monkeypatch):
    monkeypatch.setattr(config, "NARRATIVE_MERGED_ROUTING_ENABLED", True)
    monkeypatch.setattr(config, "NARRATIVE_DECISION_API_KEY", "")
    monkeypatch.setattr(config, "NARRATIVE_DECISION_API_BASE", DECISION_HOST_BASE)


@pytest.fixture
def wire(monkeypatch):
    """The REAL decision client over a fake transport. Records (auth header,
    request body) per call and answers "stay" (anchor 0.9, all else 0.0)."""
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append({"auth": req.headers.get("authorization"), "body": body})
        answers = {
            key: {"type": "noul", "noul": 0.9 if key == "anchor" else 0.0}
            for key in body["questions"]
        }
        return httpx.Response(200, json={
            "answers": answers, "model": body["model"],
            "usage": {"input_tokens": 333, "output_tokens": 55},
        })

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        decision_model.httpx, "AsyncClient",
        lambda *a, **k: real_client(transport=transport, **{
            k2: v for k2, v in k.items() if k2 != "transport"
        }),
    )
    return seen


async def _as_owner(openai: OpenAIConfig, turn):
    """Run ``turn()`` in its own task with the agent owner's provider configs
    installed — the way the turn's ingress scopes them per turn."""
    async def run():
        set_user_config(
            ClaudeConfig(), openai, CodexConfig(), None, None,
        )
        return await turn()

    return await asyncio.create_task(run())


def _two_call_continuity(service, monkeypatch) -> None:
    """Replace the continuity land mine with a stub that says "continuous", so
    a turn sent down the two-call path lands without any LLM."""
    from narranexus.platform.narrative.models import ContinuityResult

    class _Detector:
        async def detect(self, **kw):
            return ContinuityResult(is_continuous=True, confidence=0.9, reason="stub")

    monkeypatch.setattr(service, "_get_continuity_detector", lambda: _Detector())


async def test_the_platform_key_wins_over_the_owners_key(
    service, db_client, merged_on, monkeypatch, wire
):
    monkeypatch.setattr(config, "NARRATIVE_DECISION_API_BASE", DECISION_HOST_BASE)
    anchor, _ = await _seed(service)
    owner = OpenAIConfig(api_key="owner-own-key", base_url=DECISION_HOST_BASE)

    result = await _as_owner(owner, lambda: service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    ))

    assert result.selection_method == "merged_continue"
    assert [c["auth"] for c in wire] == [f"Bearer {PLATFORM_KEY}"]


async def test_an_owners_helper_key_on_the_decision_host_is_sent_as_its_own(
    service, db_client, merged_on_no_platform_key, wire
):
    anchor, _ = await _seed(service)
    owner = OpenAIConfig(api_key="owner-own-key", base_url=DECISION_HOST_BASE)

    result = await _as_owner(owner, lambda: service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    ))

    assert result.selection_method == "merged_continue"
    assert [c["auth"] for c in wire] == ["Bearer owner-own-key"]
    row = await _row(db_client)
    assert row["merged_call"] == 1


@pytest.mark.parametrize(
    "owner",
    [
        OpenAIConfig(api_key="sk-virtual", base_url="http://llm-gateway:4000/v1"),
        OpenAIConfig(api_key="sk-or", base_url="https://openrouter.ai/api/v1"),
        OpenAIConfig(),  # no credential at all
    ],
    ids=["internal_gateway", "other_aggregator", "none"],
)
async def test_without_a_qualifying_credential_the_turn_takes_the_two_call_path(
    service, db_client, merged_on_no_platform_key, monkeypatch, wire, owner
):
    anchor, _ = await _seed(service)
    _two_call_continuity(service, monkeypatch)

    result = await _as_owner(owner, lambda: service.select(
        AGENT, USER, ANCHOR_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    ))

    assert wire == [], "the decision model must not be asked"
    assert result.selection_method == "continuous"
    row = await _row(db_client)
    assert row["continuity_ran"] == 1
    assert not row["merged_call"]


async def test_concurrent_turns_of_different_owners_each_send_their_own_key(
    service, db_client, merged_on_no_platform_key, wire
):
    await _seed(service)
    turns = {
        "user_a": ("帮我订下周去东京的机票", "key-of-user-a"),
        "user_b": ("餐厅推荐一下吧", "key-of-user-b"),
    }

    async def turn(user_id, message, key):
        return await _as_owner(
            OpenAIConfig(api_key=key, base_url=DECISION_HOST_BASE),
            lambda: service.select(
                AGENT, user_id, message, session=_session(None),
                trigger="chat", is_user_chat=True,
            ),
        )

    await asyncio.gather(*(turn(u, m, k) for u, (m, k) in turns.items()))

    sent = {c["body"]["state"]["current_message"]: c["auth"] for c in wire}
    assert sent == {m: f"Bearer {k}" for m, k in turns.values()}


# ===================================================================== #
# Cost: a borrowed key is the owner's spend; the platform key is not    #
# ===================================================================== #


async def _cost_rows(db_client):
    return await db_client.get("cost_records", {"agent_id": AGENT})


async def _as_owner_with_cost_context(owner: OpenAIConfig, db_client, turn):
    from narranexus.platform.agent_framework.api_config import set_provider_card_sources
    from narranexus.platform.utils.cost_tracker import cost_context_scope

    async def with_ledger():
        set_provider_card_sources({"agent": "netmind", "helper_llm": "netmind"})
        with cost_context_scope(AGENT, db_client):
            return await turn()

    return await _as_owner(owner, with_ledger)


async def test_a_borrowed_owner_key_books_one_cost_row_to_the_helper_card(
    service, db_client, merged_on_no_platform_key, wire
):
    from narranexus.platform.narrative._narrative_impl.merged_select import DECISION_CALL_TYPE

    anchor, _ = await _seed(service)
    owner = OpenAIConfig(api_key="owner-own-key", base_url=DECISION_HOST_BASE)

    await _as_owner_with_cost_context(owner, db_client, lambda: service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    ))

    rows = await _cost_rows(db_client)
    assert len(rows) == 1
    row = rows[0]
    assert row["call_type"] == DECISION_CALL_TYPE
    assert row["model"] == config.NARRATIVE_DECISION_MODEL
    assert (row["input_tokens"], row["output_tokens"]) == (333, 55)
    assert row["provider_card_source"] == "netmind", "attributed to the helper card that paid"


async def test_the_platform_key_books_nothing_to_the_owner(
    service, db_client, merged_on, monkeypatch, wire
):
    monkeypatch.setattr(config, "NARRATIVE_DECISION_API_BASE", DECISION_HOST_BASE)
    anchor, _ = await _seed(service)
    owner = OpenAIConfig(api_key="owner-own-key", base_url=DECISION_HOST_BASE)

    await _as_owner_with_cost_context(owner, db_client, lambda: service.select(
        AGENT, USER, FOREIGN_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    ))

    assert [c["auth"] for c in wire] == [f"Bearer {PLATFORM_KEY}"]
    assert await _cost_rows(db_client) == []


async def test_a_free_tier_gateway_owner_is_never_asked_nor_charged(
    service, db_client, merged_on_no_platform_key, monkeypatch, wire
):
    """The free-tier card's base is the internal gateway: never borrowed, so
    no decision call and no cost row can ever hit its quota."""
    anchor, _ = await _seed(service)
    _two_call_continuity(service, monkeypatch)
    owner = OpenAIConfig(api_key="sk-virtual", base_url="http://llm-gateway:4000/v1")

    await _as_owner_with_cost_context(owner, db_client, lambda: service.select(
        AGENT, USER, ANCHOR_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    ))

    assert wire == []
    assert await _cost_rows(db_client) == []


# ===================================================================== #
# Credential resolution that breaks still routes                        #
# ===================================================================== #


async def test_a_resolution_error_takes_the_two_call_path(
    service, db_client, merged_on, monkeypatch
):
    anchor, _ = await _seed(service)
    _two_call_continuity(service, monkeypatch)
    _no_decider(monkeypatch)

    def _broken():
        raise RuntimeError("provider config store unavailable")

    monkeypatch.setattr(merged_router, "decision_endpoint_for_turn", _broken)

    result = await service.select(
        AGENT, USER, ANCHOR_QUERY, session=_session(anchor.id),
        trigger="chat", is_user_chat=True,
    )

    assert result.selection_method == "continuous"
    row = await _row(db_client)
    assert not row["merged_call"]


# ===================================================================== #
# The decision base derives from the NetMind inference environment      #
# ===================================================================== #


def _decision_base(**env: str) -> str:
    probe = subprocess.run(
        [sys.executable, "-c",
         "from narranexus.platform.narrative.config import config;"
         "print(config.NARRATIVE_DECISION_API_BASE)"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ":".join(sys.path), **env},
    )
    assert probe.returncode == 0, probe.stderr
    return probe.stdout.strip().splitlines()[-1]


def test_the_decision_base_derives_from_the_netmind_inference_base():
    assert _decision_base(NETMIND_INFERENCE_BASE="https://test.api.netmind.ai/inference-api") == (
        "https://test.api.netmind.ai/inference-api/openai/v1"
    )


def test_an_explicit_decision_base_overrides_the_derivation():
    assert _decision_base(
        NETMIND_INFERENCE_BASE="https://test.api.netmind.ai/inference-api",
        NARRATIVE_DECISION_API_BASE="https://decisions.example/v1",
    ) == "https://decisions.example/v1"


def test_with_nothing_set_the_decision_base_is_the_prod_netmind_openai_base():
    from narranexus.platform.settings import netmind_inference_url

    assert _decision_base() == netmind_inference_url("openai", "https://api.netmind.ai/inference-api")


def test_startup_validation_runs_on_the_derived_base():
    probe = _boot(NARRATIVE_MERGED_ROUTING_ENABLED="1", NETMIND_INFERENCE_BASE="not-a-url")
    assert probe.returncode != 0
    assert "NARRATIVE_DECISION_API_BASE" in probe.stderr

