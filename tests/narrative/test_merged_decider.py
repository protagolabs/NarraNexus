"""
@file_name: test_merged_decider.py
@date: 2026-11-25
@description: The merged-routing decider in isolation — the pure score->verdict
rule, the question set built per turn, and `decide`'s never-raise contract.

The end-to-end flow over the real landings lives in test_merged_routing.py;
here each unit is exercised alone, with the decision client stubbed only in
the `decide` tests.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from narranexus.platform.agent_framework.llm.decision_model import (
    DecisionEndpoint,
    DecisionModelError,
    DecisionResult,
    encode_questions,
    serialise,
)
from narranexus.platform.narrative._narrative_impl import merged_router as mr
from narranexus.platform.narrative.config import config
from narranexus.platform.narrative.models import Narrative, NarrativeInfo, NarrativeType

THRESHOLDS = mr.DecisionThresholds(stay=0.2, switch=0.3, no_topic=0.5)
ENDPOINT = DecisionEndpoint("https://decisions.test/v1", "k", "m", 3.0)


def _verdict(scores, *, anchor=True, participants=0, menu=0, thresholds=THRESHOLDS):
    return mr.verdict_from_scores(
        {"no_topic": 0.0, **scores},
        offers_anchor=anchor,
        candidate_counts={mr.VERDICT_PARTICIPANT: participants, mr.VERDICT_MATCH: menu},
        thresholds=thresholds,
    )


# ── the threshold rule ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "scores,kwargs,expected",
    [
        # switch: best other clears SWITCH and beats the anchor
        ({"anchor": 0.1, "m0": 0.31}, dict(menu=1), ("match", 0)),
        ({"anchor": 0.1, "p0": 0.2, "p1": 0.9}, dict(participants=2), ("participant", 1)),
        # exactly at SWITCH counts
        ({"anchor": 0.0, "m0": 0.3}, dict(menu=1), ("match", 0)),
        # just below SWITCH does not switch; anchor >= STAY stays
        ({"anchor": 0.25, "m0": 0.2999}, dict(menu=1), ("continue_anchor", -1)),
        # a tie with the anchor stays (switch needs strictly greater)
        ({"anchor": 0.6, "m0": 0.6}, dict(menu=1), ("continue_anchor", -1)),
        # anchor exactly at STAY stays
        ({"anchor": 0.2}, {}, ("continue_anchor", -1)),
        # anchor below STAY: no_topic at >= NO_TOPIC
        ({"anchor": 0.19, "no_topic": 0.5}, {}, ("no_topic", -1)),
        # nothing clears -> new
        ({"anchor": 0.19, "no_topic": 0.49}, {}, ("new", -1)),
        # high other but anchor higher -> stay
        ({"anchor": 0.95, "p0": 0.9, "m0": 0.8}, dict(participants=1, menu=1),
         ("continue_anchor", -1)),
    ],
)
def test_every_branch_of_the_rule(scores, kwargs, expected):
    got = _verdict(scores, **kwargs)
    assert (got.verdict, got.match_index) == expected


def test_a_participant_wins_a_tie_with_a_menu_row():
    got = _verdict({"anchor": 0.0, "m0": 0.7, "p0": 0.7}, participants=1, menu=1)
    assert (got.verdict, got.match_index) == ("participant", 0)


def test_within_a_kind_the_lowest_index_wins_a_tie():
    got = _verdict({"anchor": 0.0, "m0": 0.4, "m1": 0.7, "m2": 0.7}, menu=3)
    assert (got.verdict, got.match_index) == ("match", 1)


def test_a_higher_menu_row_beats_a_lower_participant():
    got = _verdict({"anchor": 0.0, "p0": 0.4, "m0": 0.8}, participants=1, menu=1)
    assert (got.verdict, got.match_index) == ("match", 0)


def test_without_an_anchor_continue_is_unreachable_and_the_score_is_not_read():
    # No "anchor" key at all: reading it would raise KeyError.
    got = _verdict({"m0": 0.25, "no_topic": 0.1}, anchor=False, menu=1)
    assert got.verdict == "new"
    got = _verdict({"m0": 0.3}, anchor=False, menu=1)
    assert (got.verdict, got.match_index) == ("match", 0)


def test_thresholds_are_inputs_not_constants():
    scores = {"anchor": 0.35}
    assert _verdict(scores).verdict == "continue_anchor"
    stricter = mr.DecisionThresholds(stay=0.4, switch=0.3, no_topic=0.5)
    assert _verdict(scores, thresholds=stricter).verdict == "new"


def test_a_missing_score_raises_rather_than_defaulting():
    with pytest.raises(KeyError):
        mr.verdict_from_scores(
            {"anchor": 0.9}, offers_anchor=True,
            candidate_counts={mr.VERDICT_MATCH: 1}, thresholds=THRESHOLDS,
        )


def test_the_reason_carries_the_winning_score():
    assert "m0=0.81" in _verdict({"anchor": 0.1, "m0": 0.81}, menu=1).reason
    assert "anchor=0.44" in _verdict({"anchor": 0.44}).reason


# ── the question set ───────────────────────────────────────────────────────


def _narrative(nid="nar_anchor", name="Deploy script errors", summary="debugging CI"):
    now = datetime.now(timezone.utc)
    return Narrative(
        id=nid, type=NarrativeType.CHAT, agent_id="a",
        narrative_info=NarrativeInfo(name=name, description="", current_summary=summary, actors=[]),
        event_ids=[], topic_keywords=["deploy"], is_special="other",
        created_at=now, updated_at=now,
    )


def _cand(nid, name, **extra):
    return {"id": nid, "type": "search", "name": name, "description": f"about {name}", **extra}


def _input(**overrides):
    base = dict(
        query="and step two?", anchor=_narrative(), anchor_is_continuable=True,
        previous_query="how do I fix the deploy script", previous_response="check the log",
        minutes_since_previous=1.54,
        menu=[_cand("nar_m0", "Restaurants", matched_terms=["fix"], matched_content="…fix…")],
        participants=[_cand("nar_p0", "Quote for client")],
    )
    base.update(overrides)
    return mr.MergedRoutingInput(**base)


def test_one_question_per_candidate_in_a_fixed_order():
    request = mr.build_decision_request(_input())
    assert [q.key for q in request.questions] == ["anchor", "p0", "m0", "no_topic"]


def test_the_anchor_question_is_not_asked_without_a_continuable_anchor():
    for inp in (_input(anchor=None, anchor_is_continuable=False),
                _input(anchor_is_continuable=False)):
        keys = [q.key for q in mr.build_decision_request(inp).questions]
        assert "anchor" not in keys


def test_the_state_carries_message_previous_turn_and_gap():
    state = mr.build_decision_request(_input()).state
    assert state == {
        "current_message": "and step two?",
        "previous_turn": {
            "user_query": "how do I fix the deploy script",
            "agent_response": "check the log",
        },
        "minutes_since_previous_turn": 1.5,
    }


def test_a_proactive_previous_turn_says_so():
    state = mr.build_decision_request(_input(previous_query="")).state
    assert state["previous_turn"] == {
        "user_query": None, "agent_response": "check the log",
        "agent_messaged_proactively": True,
    }
    state = mr.build_decision_request(_input(previous_query="", previous_response="")).state
    assert state["previous_turn"] is None


def test_each_thread_question_shows_its_surface_and_the_shared_rules():
    request = mr.build_decision_request(_input())
    by_key = {q.key: q.instructions for q in request.questions}
    assert by_key["anchor"]["thread"] == {
        "name": "Deploy script errors", "summary": "debugging CI",
        "is_thread_of_previous_turn": True,
    }
    assert by_key["p0"]["thread"]["user_was_invited_into_this_thread"] is True
    assert by_key["m0"]["thread"]["matched_terms"] == ["fix"]
    assert by_key["m0"]["thread"]["matched_excerpt"] == "…fix…"
    for key in ("anchor", "p0", "m0"):
        assert by_key[key]["question"] == mr.THREAD_QUESTION
        assert by_key[key]["rules"] == list(mr.ROUTING_RULES)
    assert by_key["no_topic"] == mr.NO_TOPIC_QUESTION


@pytest.mark.parametrize(
    "field,cap,code",
    [
        ("query", "MERGED_QUERY_MAX_CHARS", "query"),
        ("previous_query", "MERGED_QUERY_MAX_CHARS", "prev_query"),
        ("previous_response", "MERGED_PREV_RESPONSE_MAX_CHARS", "prev_response"),
    ],
)
def test_a_clipped_field_is_head_kept_and_reported(monkeypatch, field, cap, code):
    monkeypatch.setattr(config, cap, 10)
    request = mr.build_decision_request(_input(**{field: "HEAD" + "x" * 50}))
    assert code in request.truncated
    assert "HEAD" in serialise(request.state)


def test_nothing_is_reported_truncated_when_nothing_was():
    assert mr.build_decision_request(_input()).truncated == ()


def test_input_chars_is_the_serialised_state_and_questions():
    request = mr.build_decision_request(_input())
    assert request.input_chars == len(serialise(
        {"state": request.state, "questions": encode_questions(request.questions)}
    ))


# ── decide ─────────────────────────────────────────────────────────────────


def _stub_client(monkeypatch, scores=None, boom=None):
    calls: list = []

    async def fake(endpoint, state, questions):
        calls.append({"endpoint": endpoint, "state": state, "questions": questions})
        if boom is not None:
            raise boom
        asked = {q.key: 0.0 for q in questions}
        asked.update({k: v for k, v in (scores or {}).items() if k in asked})
        return DecisionResult(asked, "typesafe/jev-1.13", 10, 2, 5)

    monkeypatch.setattr(mr, "ask_yes_no", fake)
    return calls


async def test_a_score_on_m2_lands_menu_index_2(monkeypatch):
    menu = [_cand(f"nar_m{i}", f"Row {i}") for i in range(3)]
    inp = _input(menu=menu, participants=[])
    _stub_client(monkeypatch, {"m2": 0.9})

    decision = await mr.decide(inp, ENDPOINT)

    assert decision.ok and decision.verdict == "match" and decision.match_index == 2
    assert mr.resolve_choice(decision, inp) == "nar_m2"


async def test_a_score_on_p1_lands_participant_index_1(monkeypatch):
    parts = [_cand(f"nar_p{i}", f"Invite {i}") for i in range(2)]
    inp = _input(participants=parts, menu=[])
    _stub_client(monkeypatch, {"p1": 0.9})

    decision = await mr.decide(inp, ENDPOINT)

    assert mr.resolve_choice(decision, inp) == "nar_p1"


async def test_the_endpoint_is_passed_through_untouched(monkeypatch):
    calls = _stub_client(monkeypatch, {"anchor": 0.9})
    await mr.decide(_input(), ENDPOINT)
    assert calls[0]["endpoint"] is ENDPOINT


async def test_a_call_failure_is_not_a_verdict(monkeypatch):
    _stub_client(monkeypatch, boom=DecisionModelError("HTTP 429"))
    decision = await mr.decide(_input(), ENDPOINT)
    assert not decision.ok and decision.verdict == mr.VERDICT_FAILED
    assert decision.request is not None and decision.result is None


async def test_an_assembly_failure_is_not_a_verdict(monkeypatch):
    calls = _stub_client(monkeypatch, {"anchor": 0.9})
    bad = _input(menu=[{"id": "x"}])  # a candidate missing its labels
    decision = await mr.decide(bad, ENDPOINT)
    assert not decision.ok and decision.request is None
    assert calls == [], "nothing is sent when the request cannot be built"


async def test_unusable_scores_are_not_a_verdict(monkeypatch):
    async def fake(endpoint, state, questions):
        return DecisionResult({"no_topic": 0.1}, "m", 0, 0, 1)  # anchor missing

    monkeypatch.setattr(mr, "ask_yes_no", fake)
    decision = await mr.decide(_input(), ENDPOINT)
    assert not decision.ok and decision.verdict == mr.VERDICT_FAILED
    assert decision.result is not None


async def test_decide_reads_thresholds_from_config(monkeypatch):
    _stub_client(monkeypatch, {"anchor": 0.25})
    assert (await mr.decide(_input(), ENDPOINT)).verdict == "continue_anchor"
    monkeypatch.setattr(config, "NARRATIVE_DECISION_STAY_THRESHOLD", 0.3)
    assert (await mr.decide(_input(), ENDPOINT)).verdict == "new"
