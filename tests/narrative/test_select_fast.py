"""
@file_name: test_select_fast.py
@date: 2026-08-06
@description: NarrativeService.select_fast — BM25 top-1 direct pick.

Locks:
- Zero LLM, zero creation, zero session writes: the method only calls
  the BM25 keyword search (top_k=1) and CRUD load_by_id.
- No candidate -> None (caller runs bare; nothing is created).
- Retrieval returning an id whose row vanished -> None (no crash).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.narrative.narrative_service import NarrativeService


def _service_with_fakes(search_results, loaded):
    service = NarrativeService.__new__(NarrativeService)
    service._retrieval = SimpleNamespace(
        _keyword_search=AsyncMock(return_value=search_results)
    )
    service._crud = SimpleNamespace(load_by_id=AsyncMock(return_value=loaded))
    return service


@pytest.mark.asyncio
async def test_top1_hit_loads_and_returns_narrative():
    hit = SimpleNamespace(narrative_id="nar_1", raw_score=5.0)
    narrative = SimpleNamespace(id="nar_1")
    service = _service_with_fakes([hit], narrative)

    result = await service.select_fast("agent_a", "user_u", "weather query")

    assert result is narrative
    service._retrieval._keyword_search.assert_awaited_once_with(
        query="weather query", user_id="user_u", agent_id="agent_a", top_k=1
    )
    service._crud.load_by_id.assert_awaited_once_with("nar_1")


@pytest.mark.asyncio
async def test_no_candidates_returns_none():
    service = _service_with_fakes([], None)
    assert await service.select_fast("a", "u", "q") is None
    service._crud.load_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_vanished_row_returns_none():
    hit = SimpleNamespace(narrative_id="nar_gone", raw_score=1.0)
    service = _service_with_fakes([hit], None)
    assert await service.select_fast("a", "u", "q") is None
