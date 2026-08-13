"""
@file_name: test_skills_env_enrich.py
@date: 2026-08-13
@description: _enrich_platform_env_status downgrades ONLY the platform half.

Regression for the false positive where enrich re-read meta by name (missing
.disabled/ dirs) and recomputed the stored half too, stomping the honest
env_configured that _parse_skill_md already produced.
"""
from __future__ import annotations

import pytest

from backend.routes import skills as skills_routes
from xyz_agent_context.schema.skill_schema import SkillInfo


class _FakeModule:
    """enrich must NOT touch stored config, so it must never read meta."""

    def get_skill_env_config(self, name):  # pragma: no cover - must not be called
        raise AssertionError("enrich re-read stored meta — it should not")


def _skill(name, requires_env, env_configured):
    return SkillInfo(
        name=name, description="", path=f"skills/{name}",
        requires_env=requires_env, env_configured=env_configured,
    )


@pytest.mark.asyncio
async def test_enrich_only_downgrades_the_platform_half(monkeypatch):
    async def _available(db, user_id):
        return {"NETMIND_API_KEY"}  # platform var IS satisfiable

    async def _db():
        return object()

    monkeypatch.setattr(skills_routes, "get_db_client", _db, raising=False)
    monkeypatch.setattr(
        "xyz_agent_context.module.skill_module.skill_module.platform_env_available",
        _available,
    )

    # A skill (could be disabled) with a platform var AND a self-stored var,
    # already computed env_configured=True by _parse_skill_md.
    kept = _skill("kept", ["NETMIND_API_KEY", "MY_KEY"], True)
    # A skill whose platform var is NOT satisfiable → downgraded.
    dropped = _skill("dropped", ["NETMIND_API_KEY"], True)

    async def _unavailable(db, user_id):
        return set()

    await skills_routes._enrich_platform_env_status(_FakeModule(), [kept], "u1")
    assert kept.env_configured is True  # stored half untouched, platform ok

    monkeypatch.setattr(
        "xyz_agent_context.module.skill_module.skill_module.platform_env_available",
        _unavailable,
    )
    await skills_routes._enrich_platform_env_status(_FakeModule(), [dropped], "u1")
    assert dropped.env_configured is False  # platform var missing → downgraded
