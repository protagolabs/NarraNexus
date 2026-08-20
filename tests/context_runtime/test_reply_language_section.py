"""
@file_name: test_reply_language_section.py
@date: 2026-08-20
@description: Reply-language system-prompt directive.

Policy history: the 2026-08-11 fix made the UI language a HARD output
constraint ("write every reply in {name}") — Shenzhen round-2 retest (手册
B4) showed the inversion it causes: UI set to Chinese, English questions
answered in Chinese. Owner decision 2026-08-20: the CURRENT message's
language wins; the UI preference is only the fallback when the message's
language is undeterminable. These tests pin that priority order and the
byte-stability that keeps the section in the cacheable prompt region.
"""
from xyz_agent_context.context_runtime.context_runtime import (
    build_reply_language_section,
)


def test_unset_yields_empty():
    assert build_reply_language_section(None) == ""
    assert build_reply_language_section("") == ""
    assert build_reply_language_section("  ") == ""


def test_known_code_names_language_and_is_stable():
    a = build_reply_language_section("zh")
    b = build_reply_language_section("zh")
    assert a == b  # byte-stable — lives in the cacheable prompt region
    assert "Chinese" in a and "(zh)" in a


def test_message_language_wins_preference_is_fallback_only():
    """The B4 contract: 中文问中文答、英文问英文答, regardless of the UI
    toggle — the preference may only decide UNDETERMINABLE cases."""
    section = build_reply_language_section("zh")
    lowered = section.lower()
    # the directive leads with the current message's language
    assert "current message" in lowered
    # the preference is explicitly scoped to the fallback role
    assert "only" in lowered and "fallback" in lowered
    # the old hard constraint must be gone
    assert "write every" not in lowered


def test_region_variant_and_unknown_code_fall_back():
    assert "Chinese" in build_reply_language_section("zh-CN")
    unknown = build_reply_language_section("tlh")
    assert "(tlh)" in unknown  # bare code still communicated
