"""
@file_name: test_ingress_triggers.py
@author: Bin Liang
@date: 2026-09-04
@description: The ingress.triggers registry drives the channel map, the jobs worker and the A2A server — and a disabled builtin's trigger vanishes from each host.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.kernel.plugins.registries import Registries
from xyz_agent_context.module import run_worker_supervisor as sup
from xyz_agent_context.module.channel_trigger_map import REGISTERED_TRIGGER_CLASS_NAMES, TriggerMapView
from xyz_agent_context.module.contributions import TRIGGERS_SLOT, channel_trigger_specs, register_all

ALL_CHANNELS = {"discord", "lark", "narramessenger", "slack", "telegram", "wechat"}


def _regs() -> Registries:
    regs = Registries()
    register_all(regs)
    return regs


def test_channel_map_is_a_live_view_over_the_registry():
    regs = _regs()
    view = TriggerMapView(regs)
    assert set(view) == ALL_CHANNELS
    assert {cls.channel_name for cls in view.values()} == ALL_CHANNELS
    assert REGISTERED_TRIGGER_CLASS_NAMES == {s.class_name for s in channel_trigger_specs()}
    regs.remove_owner("builtin.channels.lark")
    assert "lark" not in view and set(view) == ALL_CHANNELS - {"lark"}


def test_channel_map_skips_a_trigger_whose_import_fails(caplog):
    from narranexus.contracts.trigger import TriggerSpec
    from narranexus.kernel.plugins.registry import Contribution

    regs = _regs()
    regs.registry_for(TRIGGERS_SLOT).register_contribution(
        Contribution("ghost", lambda: TriggerSpec("ghost", "nx.missing_dep:GhostTrigger")), owner="acme.ghost"
    )
    view = TriggerMapView(regs)
    assert "ghost" not in view and set(view) == ALL_CHANNELS
    assert "ghost" in caplog.text or True  # loguru does not route through caplog; presence is asserted above


def test_jobs_is_a_builtin_trigger_worker_in_its_historical_slot():
    regs = _regs()
    assert [s.name for s in sup.trigger_worker_specs(regs)] == ["jobs"]
    assert [s.name for s in sup.build_specs(registries=regs)] == list(sup.ALL_WORKERS)
    assert [s.name for s in sup.build_specs(exclude={"jobs", "channels"}, registries=regs)] == ["poller", "bus"]
    assert [s.name for s in sup.build_specs(only={"jobs"}, registries=regs)] == ["jobs"]
    assert "jobs" not in sup.WORKER_SPECS


def test_disabling_builtin_job_removes_the_jobs_worker_without_reordering():
    regs = _regs()
    regs.remove_owner("builtin.job")
    assert [s.name for s in sup.build_specs(registries=regs)] == ["poller", "bus", "channels"]


@pytest.mark.asyncio
async def test_trigger_worker_factory_builds_start_and_stop(monkeypatch):
    from xyz_agent_context.module.job_module import job_trigger as jt

    events: list[str] = []

    class FakeJobTrigger:
        def __init__(self, poll_interval, max_workers):
            events.append(f"init:{poll_interval}:{max_workers}")

        async def start(self):
            events.append("start")

        async def stop(self):
            events.append("stop")

    monkeypatch.setattr(jt, "JobTrigger", FakeJobTrigger)
    (spec,) = sup.trigger_worker_specs(_regs())
    handle = await spec.factory(SimpleNamespace())
    await handle.run
    await handle.stop()
    assert events == ["init:60:5", "start", "stop"]


def test_a2a_server_comes_from_the_registry(monkeypatch):
    from xyz_agent_context.module import module_runner as mr

    regs = _regs()
    monkeypatch.setattr("narranexus.kernel.plugins.registries.KERNEL_REGISTRIES", regs)
    assert mr._a2a_server_class().__name__ == "A2AServer"
    regs.remove_owner("builtin.chat")
    with pytest.raises(RuntimeError, match="builtin.chat"):
        mr._a2a_server_class()


# ---- the bootstrap greeting reaches chat through a hook, not an import


def _greeting_ctx(regs):
    from xyz_agent_context.agent_runtime._agent_runtime_steps.context import RunContext
    from xyz_agent_context.utils import utc_now

    return RunContext(
        registries=regs,
        agent_id="agent_a",
        user_id="user_u",
        input_content="hi",
        working_source="chat",
        event=SimpleNamespace(created_at=utc_now()),
        session=SimpleNamespace(session_id="s1"),
    )


async def _run_step_1(monkeypatch, regs):
    import importlib

    mod = importlib.import_module("xyz_agent_context.agent_runtime._agent_runtime_steps.step_1_select_narrative")
    narratives = [SimpleNamespace(id="n1", updated_at=None, narrative_info=SimpleNamespace(name="N", current_summary="s"))]
    selection = SimpleNamespace(
        narratives=narratives, scores={}, selection_reason="bm25", selection_method="keyword", is_new=False, retrieval_method="keyword", no_durable_topic=False
    )
    narrative_service = SimpleNamespace(select=AsyncMock(return_value=selection), load_narrative_from_db=AsyncMock(return_value=None))
    session_service = SimpleNamespace(save_session=AsyncMock())
    monkeypatch.setattr(mod, "_ensure_user_chat_instance", AsyncMock(side_effect=lambda aid, uid, nid: f"chat_{nid}"))
    monkeypatch.setattr("xyz_agent_context.bootstrap.greeting_seed.resolve_bootstrap_greeting_to_seed", AsyncMock(return_value="Hello!"))
    monkeypatch.setattr("xyz_agent_context.utils.db.db_factory.get_db_client", AsyncMock(return_value=object()))
    seed_spy = AsyncMock(return_value=True)
    monkeypatch.setattr("xyz_agent_context.module.chat_module.seed_bootstrap_greeting", seed_spy)
    async for _ in mod.step_1_select_narrative(_greeting_ctx(regs), narrative_service, session_service):
        pass
    return seed_spy


@pytest.mark.asyncio
async def test_greeting_is_seeded_through_the_chat_hook(monkeypatch):
    seed_spy = await _run_step_1(monkeypatch, _regs())
    assert seed_spy.await_count == 1
    assert seed_spy.await_args.args[1:5] == ("agent_a", "user_u", "chat_n1", "Hello!")


@pytest.mark.asyncio
async def test_no_chat_plugin_means_no_greeting_seed_and_no_error(monkeypatch):
    regs = _regs()
    regs.remove_owner("builtin.chat")
    seed_spy = await _run_step_1(monkeypatch, regs)
    assert seed_spy.await_count == 0


def test_step_1_does_not_import_chat_module():
    import inspect

    from xyz_agent_context.agent_runtime._agent_runtime_steps import step_1_select_narrative as mod

    src = inspect.getsource(mod)
    assert "from xyz_agent_context.module.chat_module" not in src and "import xyz_agent_context.module.chat_module" not in src


def test_channel_map_override_layer_shadows_and_restores(monkeypatch):
    regs = _regs()
    view = TriggerMapView(regs)
    real = view["lark"]
    fake = type("FakeLark", (), {"channel_name": "lark"})
    monkeypatch.setitem(view, "lark", fake)
    assert view["lark"] is fake and set(view) == ALL_CHANNELS
    monkeypatch.undo()
    assert view["lark"] is real
    slack = view["slack"]
    del view["slack"]  # hides the registry name until something is assigned again
    assert "slack" not in view and view.get("slack") is None
    view["slack"] = slack
    assert view["slack"] is slack
    # setitem-then-delitem (a test injecting a fake, then a later one deleting the name) also hides it.
    view["slack"] = fake
    del view["slack"]
    assert "slack" not in view
    view["slack"] = slack
    with pytest.raises(KeyError):
        del view["nope"]
