"""
@file_name: test_executor_service_warmup.py
@date: 2026-08-20
@description: The executor's startup lifespan primes the nexus_power warm-runner
pool BEFORE serving requests, so the process's first turn is not cold. Warmup is
best-effort: a warmup failure must never prevent the executor from starting.
"""
from fastapi.testclient import TestClient

from xyz_agent_context.agent_runtime import executor_service


def test_startup_warms_nexus_power_pool(monkeypatch):
    seen = {"framework": None, "warmup": 0}

    class FakeDriver:
        def warmup(self):
            seen["warmup"] += 1

    def fake_get(framework, **kwargs):
        seen["framework"] = framework
        return FakeDriver()

    monkeypatch.setattr(executor_service, "get_agent_loop_driver", fake_get)
    with TestClient(executor_service.app):  # entering triggers startup lifespan
        pass
    assert seen["framework"] == "nexus_power"
    assert seen["warmup"] == 1


def test_startup_survives_warmup_failure(monkeypatch):
    def boom(framework, **kwargs):
        raise RuntimeError("warmup blew up")

    monkeypatch.setattr(executor_service, "get_agent_loop_driver", boom)
    # Must NOT raise — executor has to start even if warmup fails.
    with TestClient(executor_service.app):
        pass


def test_startup_tolerates_driver_without_warmup(monkeypatch):
    # A remote/other driver may not implement warmup(); startup must skip it.
    class NoWarmupDriver:
        pass

    monkeypatch.setattr(
        executor_service, "get_agent_loop_driver", lambda framework, **kw: NoWarmupDriver()
    )
    with TestClient(executor_service.app):
        pass
