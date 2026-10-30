"""
@file_name: test_requirements_extraction_confined.py
@author: Bin Liang
@date: 2026-10-30
@description: _extract_requirements_via_llm reads SKILL.md through the
link-free walk. It runs right after an agent turn in the tenant's own
executor, which can swap skills/<name> for a link into another tenant's
tree; the victim's SKILL.md must never reach the helper LLM.
"""
from __future__ import annotations

import os

import pytest

from narranexus.platform.settings import settings
from narranexus_plugins.skill_module import routes
from narranexus_plugins.skill_module.skill_module import SkillModule


@pytest.fixture
def calls(monkeypatch):
    seen: list[str] = []

    class _Cfg:
        api_key = ""   # the function returns right after reading when no key

    monkeypatch.setattr("narranexus.platform.agent_framework.api_config.openai_config", _Cfg())
    real = SkillModule._read_confined_text

    def spy(self, path):
        text = real(self, path)
        seen.append(text)
        return text

    monkeypatch.setattr(SkillModule, "_read_confined_text", spy)
    return seen


async def test_swapped_skill_dir_never_reads_the_victims_skill_md(tmp_path, monkeypatch, calls):
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    sm = SkillModule(agent_id="agent_a", user_id="u1")
    victim = tmp_path / "u2" / "agent_v" / "skills" / "s"
    victim.mkdir(parents=True)
    (victim / "SKILL.md").write_text("VICTIM SECRET", encoding="utf-8")
    sm.skills_dir.mkdir(parents=True)
    os.symlink(victim, sm.skills_dir / "s")
    await routes._extract_requirements_via_llm(sm, "s", str(sm.skills_dir / "s"))
    assert calls == [None]          # refused, nothing read


async def test_honest_skill_md_is_read(tmp_path, monkeypatch, calls):
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    sm = SkillModule(agent_id="agent_a", user_id="u1")
    own = sm.skills_dir / "s"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("mine", encoding="utf-8")
    await routes._extract_requirements_via_llm(sm, "s", str(own))
    assert calls == ["mine"]
