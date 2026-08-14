"""
@file_name: test_reply_language_section.py
@date: 2026-08-11
@description: Reply-language system-prompt directive (fix: UI language
never reached the model). Pure-function contract: empty when unset,
byte-stable, names the language, permits explicit per-message override.
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
    assert "explicitly asks" in a  # per-message override stays possible


def test_region_variant_and_unknown_code_fall_back():
    assert "Chinese" in build_reply_language_section("zh-CN")
    unknown = build_reply_language_section("tlh")
    assert "(tlh)" in unknown  # bare code still communicated
