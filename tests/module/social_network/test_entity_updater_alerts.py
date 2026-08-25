"""
@file_name: test_entity_updater_alerts.py
@author:
@date: 2026-08-24
@description: Memory-write failures must be distinguishable and reported.

Every handler in ``_entity_updater`` used to catch, log, and return an
empty-ish value. The caller could not tell "the LLM found nothing worth
remembering" from "the LLM is dead", and skipped the write either way —
so a broken helper key degraded long memory invisibly. The 8/14 ping-pong
incident is the downstream cost: the agent's profile for the peer it had
exchanged 66,000 messages with still read "first time meeting", so
nothing in its own context could tell it it was in a loop.

Two properties pinned here:
  1. failure is distinguishable from emptiness (``None`` vs ``""``)
  2. failure is reported to the background-failure surface
"""
from __future__ import annotations

import pytest

import xyz_agent_context.module.social_network_module._entity_updater as eu

pytestmark = pytest.mark.asyncio


class _BoomSDK:
    """A helper SDK whose LLM is dead — the 2026-07 expired-key shape."""

    async def llm_function(self, **kwargs):
        raise RuntimeError("401 Unauthorized: invalid api key")


class _EmptySDK:
    """A helper SDK that works fine and finds nothing worth saying."""

    def __init__(self, output):
        self._output = output

    async def llm_function(self, **kwargs):
        class _R:
            final_output = self._output

        return _R()


@pytest.fixture
def captured(monkeypatch):
    """Capture what the file reports instead of writing to the DB."""
    calls = {"llm": [], "write": []}

    async def _fake_llm(*, source, error, agent_id, source_id=""):
        calls["llm"].append(
            {"source": source, "error": error, "agent_id": agent_id,
             "source_id": source_id}
        )

    async def _fake_write(*, operation, error, entity_id, instance_id):
        calls["write"].append({"operation": operation, "error": error})

    monkeypatch.setattr(eu, "_report_llm_failure", _fake_llm)
    monkeypatch.setattr(eu, "_report_write_failure", _fake_write)
    return calls


# ── Property 1: failure is distinguishable from emptiness ─────────────

async def test_summary_returns_none_when_the_llm_fails(monkeypatch, captured):
    monkeypatch.setattr(eu, "get_helper_sdk", lambda: _BoomSDK())
    result = await eu.summarize_new_entity_info("hi", "hello", agent_id="agt_1")
    assert result is None, "failure must not look like 'nothing to remember'"


async def test_summary_returns_empty_string_when_there_is_nothing_to_say(
    monkeypatch, captured
):
    monkeypatch.setattr(
        eu, "get_helper_sdk", lambda: _EmptySDK(eu.SummaryOutput(summary="  "))
    )
    result = await eu.summarize_new_entity_info("hi", "hello", agent_id="agt_1")
    assert result == ""
    assert not captured["llm"], "a clean empty result is not a failure"


async def test_persona_returns_none_when_the_llm_fails(monkeypatch, captured):
    monkeypatch.setattr(eu, "get_helper_sdk", lambda: _BoomSDK())
    entity = _entity(persona="Warm and concise.")
    result = await eu.infer_persona(entity=entity, agent_id="agt_1")
    assert result is None, (
        "returning the CURRENT persona made a dead LLM look exactly like a "
        "persona that simply was not changing"
    )


# ── Property 2: failure is reported ───────────────────────────────────

async def test_summary_failure_is_reported(monkeypatch, captured):
    monkeypatch.setattr(eu, "get_helper_sdk", lambda: _BoomSDK())
    await eu.summarize_new_entity_info("hi", "hello", agent_id="agt_1")

    assert len(captured["llm"]) == 1
    call = captured["llm"][0]
    assert call["source"] == "entity_summary"
    assert call["agent_id"] == "agt_1"


async def test_persona_failure_is_reported(monkeypatch, captured):
    monkeypatch.setattr(eu, "get_helper_sdk", lambda: _BoomSDK())
    await eu.infer_persona(entity=_entity(), agent_id="agt_1")
    assert [c["source"] for c in captured["llm"]] == ["persona_inference"]


async def test_dedup_failure_is_reported(monkeypatch, captured):
    monkeypatch.setattr(eu, "get_helper_sdk", lambda: _BoomSDK())
    decision, matched = await eu.decide_merge_or_create(
        "Bob", "a person", [], [_entity(), _entity()], agent_id="agt_1"
    )
    # The fallback SHAPE is unchanged — better a duplicate node than a
    # lost entity — but it is no longer free of consequence.
    assert decision == "CREATE_NEW"
    assert matched is None
    assert [c["source"] for c in captured["llm"]] == ["entity_dedup"]


async def test_extraction_failure_is_reported(monkeypatch, captured):
    monkeypatch.setattr(eu, "get_helper_sdk", lambda: _BoomSDK())
    result = await eu.extract_mentioned_entities("hi", "hello", agent_id="agt_1")
    assert result == []
    assert [c["source"] for c in captured["llm"]] == ["entity_extraction"]


async def test_compression_failure_is_reported_even_though_it_returns_text(
    monkeypatch, captured
):
    """The truncation fallback returns a plausible-looking value while
    silently dropping everything past the cut."""
    monkeypatch.setattr(eu, "get_helper_sdk", lambda: _BoomSDK())
    long_text = "x" * 3000
    result = await eu.compress_description(long_text, agent_id="agt_1")
    assert result.endswith("...")
    assert [c["source"] for c in captured["llm"]] == ["description_compression"]


async def test_db_write_failure_is_audited_not_paged(captured):
    """A failed UPDATE is our bug or infra, not something an owner fixes
    by rotating a key — audit row, no inbox notice."""

    class _BoomRepo:
        async def increment_interaction(self, **kwargs):
            raise RuntimeError("database is locked")

    await eu.update_interaction_stats(_BoomRepo(), "ent_1", "sn_1")
    assert [c["operation"] for c in captured["write"]] == ["update_interaction_stats"]
    assert not captured["llm"], "a DB failure must not page the owner"


async def test_persona_write_failure_is_audited(captured):
    class _BoomRepo:
        async def update_entity_info(self, **kwargs):
            raise RuntimeError("database is locked")

    await eu.update_entity_persona(_BoomRepo(), "ent_1", "sn_1", "Warm.")
    assert [c["operation"] for c in captured["write"]] == ["update_entity_persona"]


# ── helpers ───────────────────────────────────────────────────────────

def _entity(persona: str = ""):
    from xyz_agent_context.repository import SocialNetworkEntity

    return SocialNetworkEntity(
        entity_id="ent_1",
        instance_id="sn_1",
        entity_name="Bob",
        entity_type="user",
        entity_description="",
        persona=persona,
        interaction_count=1,
    )
