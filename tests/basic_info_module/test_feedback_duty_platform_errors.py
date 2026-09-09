"""
@file_name: test_feedback_duty_platform_errors.py
@date: 2026-09-09
@description: Regression guard for the "Product Feedback Duty" section of the
BasicInfo system-prompt template and the submit_feedback tool description.

Prod 2026-09-09: an agent's platform-run CLI answered ``agent-token-invalid``
(root cause: the platform sent the token to the wrong backend). The agent never
filed feedback (the duty only fired on user complaints / two failures of the
same instruction), asserted a wrong diagnosis to the user as fact ("the platform
cached an expired token"), and pasted the raw token into chat to "compare" it.
The template must (1) make a rejected PLATFORM-injected credential a feedback
trigger while excluding expected answers (``no_credential`` = not bound,
by-design policy refusals, a user-typed secret being rejected), (2) forbid
asserting a cause the agent cannot verify for those errors only, (3) forbid
pasting tokens / credential files into messages.
"""
from __future__ import annotations

import pytest


def _templates() -> list[str]:
    # Both the legacy template and the relocated STABLE one are rendered in
    # prod depending on settings; the rule must live in both. Prose is
    # hard-wrapped, so fold whitespace before phrase checks.
    from narranexus_plugins.basic_info_module.prompts import (
        BASIC_INFO_MODULE_INSTRUCTIONS,
        BASIC_INFO_MODULE_INSTRUCTIONS_STABLE,
    )

    return [" ".join(t.split()) for t in (
        BASIC_INFO_MODULE_INSTRUCTIONS, BASIC_INFO_MODULE_INSTRUCTIONS_STABLE,
    )]


def _duty_section(text: str) -> str:
    start = text.index("#### Product Feedback Duty")
    return text[start: text.index(" --- ", start)]


def _tool_description() -> str:
    from narranexus_plugins.basic_info_module import _basic_info_mcp_tools as mt

    captured: dict = {}

    class _Mcp:
        def tool(self, **kw):
            captured[kw["name"]] = kw["description"]
            return lambda fn: fn

    mt._register_feedback_tool(_Mcp())
    return captured["submit_feedback"]


@pytest.mark.parametrize("text", _templates())
def test_rejected_platform_credential_is_a_feedback_trigger(text):
    duty = _duty_section(text)
    third = duty[duty.index("3. "):duty.index("Rules:")]
    assert "agent-token-invalid" in third
    assert "category `error`" in third
    # Fires even when a workaround later succeeds, once per tool + code.
    assert "workaround" in third
    assert "ONCE per conversation" in third


@pytest.mark.parametrize("text", _templates())
def test_expected_answers_are_excluded_from_the_trigger(text):
    duty = _duty_section(text)
    third = duty[duty.index("3. "):duty.index("Rules:")]
    # no_credential / not bound is the normal reply of every channel tool
    # when nothing is bound; official-agent-required is a by-design policy
    # answer; a user-typed secret being rejected is the user's fix, not ours.
    assert "does NOT cover" in third
    assert "`no_credential`" in third
    assert "official-agent-required" in third
    assert "user just typed" in third


@pytest.mark.parametrize("text", _templates())
def test_no_diagnosis_rule_is_scoped_to_platform_injected_credentials(text):
    duty = _duty_section(text)
    para = duty[duty.index("Be conservative"):]
    assert "do NOT assert a diagnosis" in para
    assert "MIGHT mean" in para
    # The carve-out: user-provided secrets and BYOK keys keep concrete guidance.
    assert "BYOK" in para
    assert "does not apply to credentials the user provided" in para


@pytest.mark.parametrize("text", _templates())
def test_prompt_forbids_pasting_credentials(text):
    para = _duty_section(text)
    para = para[para.index("Be conservative"):]
    assert "never paste a token" in para
    assert "credential file" in para


def test_tool_description_names_the_platform_error_trigger():
    desc = _tool_description()
    assert "(c)" in desc
    assert "agent-token-invalid" in desc
    assert "category `error`" in desc
    assert "once per conversation" in desc
    assert "Not (c)" in desc and "no_credential" in desc
