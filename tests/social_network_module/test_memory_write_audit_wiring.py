"""
@file_name: test_memory_write_audit_wiring.py
@author:
@date: 2026-08-25
@description: The CALLER-side memory-write failures leave audit rows too.

`_entity_updater`'s own eight handlers are covered by
`test_entity_updater_alerts.py`. But memory writing does not only happen in
that file: `social_network_module` creates the primary entity and every
mentioned third-party entity itself, and those failures used to be
log-only — invisible to the `agent_runtime` alert layer because the hook
swallows at its outer edge.

Those two reporting call sites shipped without tests, which is the same
"new glue, zero coverage" gap this PR had already been sent back for one
layer down. Hence this file.

**Patch target matters.** `social_network_module.py` imports
`_report_write_failure` at module top level, so it holds a BOUND reference:
`monkeypatch.setattr(_entity_updater, "_report_write_failure", ...)` — what
the sibling test file does — has no effect here and would leave the
assertions silently passing against an empty list. Patch the attribute on
`social_network_module` instead.
"""
from __future__ import annotations

import pytest

import xyz_agent_context.module.social_network_module.social_network_module as snm
from xyz_agent_context.module.social_network_module._entity_updater import (
    ExtractedEntity,
)
from xyz_agent_context.module.social_network_module.social_network_module import (
    SocialNetworkModule,
)


@pytest.fixture
def audited(monkeypatch):
    rows = []

    async def _fake(*, operation, error, entity_id, instance_id, agent_id=""):
        rows.append(
            {
                "operation": operation,
                "entity_id": entity_id,
                "instance_id": instance_id,
                "agent_id": agent_id,
                "error": error,
            }
        )

    # NOT `_entity_updater._report_write_failure` — see the module docstring.
    monkeypatch.setattr(snm, "_report_write_failure", _fake)
    return rows


def _module(repo) -> SocialNetworkModule:
    module = SocialNetworkModule(
        agent_id="agent_mine", database_client=None, instance_id="sn_1"
    )
    module._social_repo = repo
    return module


class _StageOneBoom:
    """Stage-1 lookup fails — the quiet one: no match found reads as "brand
    new", so every turn forks another duplicate node."""

    async def search_by_name_or_alias(self, **kwargs):
        raise RuntimeError("database is locked")


@pytest.mark.asyncio
async def test_mentioned_entity_failure_is_audited(audited):
    module = _module(_StageOneBoom())

    await module._process_mentioned_entities(
        module._social_repo, "sn_1", [ExtractedEntity(name="Bob")]
    )

    assert len(audited) == 1
    row = audited[0]
    assert row["operation"] == "process_mentioned_entity"
    assert row["agent_id"] == "agent_mine"
    assert row["instance_id"] == "sn_1"
    assert row["entity_id"] == "entity_bob", (
        "the audit row must name the entity that failed — not the previous "
        "one, and not blow up because the id was assigned inside the try"
    )


@pytest.mark.asyncio
async def test_each_failing_entity_gets_its_own_row_with_its_own_id(audited):
    """Pins the `entity_id_candidate` binding: a stale value here would file
    every failure under whichever entity happened to be processed first."""
    module = _module(_StageOneBoom())

    await module._process_mentioned_entities(
        module._social_repo,
        "sn_1",
        [ExtractedEntity(name="Bob"), ExtractedEntity(name="Alice Smith")],
    )

    assert [r["entity_id"] for r in audited] == ["entity_bob", "entity_alice_smith"]


@pytest.mark.asyncio
async def test_one_failing_entity_does_not_abort_the_batch(audited):
    """The loop must keep going — and must keep auditing — after one entity
    fails, or a single bad row silently drops the rest of the batch."""
    seen = []

    class _BoomOnBob:
        async def search_by_name_or_alias(self, **kwargs):
            name = kwargs.get("name") or kwargs.get("entity_name") or ""
            seen.append(name)
            if "Bob" in str(kwargs):
                raise RuntimeError("database is locked")
            return []

        async def add_entity(self, **kwargs):
            return None

    module = _module(_BoomOnBob())
    await module._process_mentioned_entities(
        module._social_repo,
        "sn_1",
        [ExtractedEntity(name="Bob"), ExtractedEntity(name="Carol")],
    )

    assert [r["entity_id"] for r in audited] == ["entity_bob"]
    assert len(seen) == 2, "the batch must continue past the failing entity"


@pytest.mark.asyncio
async def test_the_two_caller_side_operations_are_the_expected_names(audited):
    """`operation` is what an operator greps weeks later; pin both spellings
    so a typo cannot ship green."""
    import inspect

    src = inspect.getsource(snm)
    assert 'operation="create_primary_entity"' in src
    assert 'operation="process_mentioned_entity"' in src
