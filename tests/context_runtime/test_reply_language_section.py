"""
@file_name: test_reply_language_section.py
@date: 2026-08-20
@description: Reply-language system-prompt directive.

Policy history: the 2026-08-11 fix made the UI language a HARD output
constraint ("write every reply in {name}") — the Shenzhen round-2 retest
(manual checklist B4) showed the inversion it causes: UI set to Chinese,
English questions answered in Chinese. Owner decision 2026-08-20, a
three-level priority pinned here: an explicit language request in the
message wins, then the CURRENT message's own language, then the configured
preference as the undeterminable-language fallback. Byte-stability keeps
the section in the cacheable prompt region.
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
    """The B4 contract: a Chinese question gets a Chinese answer, an
    English question an English one, regardless of the UI toggle — the
    preference may only decide UNDETERMINABLE cases. Assertions pin the
    ACTUAL phrases, not word co-occurrence (a rewrite that keeps the words
    but loses the semantics must go red)."""
    section = build_reply_language_section("zh")
    lowered = section.lower()
    assert "current message" in lowered
    assert "only as the fallback" in lowered
    assert "no determinable language" in lowered
    # the old hard constraint must be gone
    assert "write every" not in lowered


def test_explicit_request_in_message_wins_over_everything():
    """Third priority level (review #335 I1): "answer this in English"
    written in Chinese must be honored — the message's own language does
    not override an explicit request inside it. The 2026-08-11 wording
    protected this branch; deleting it silently was the regression this
    test prevents from recurring."""
    lowered = build_reply_language_section("zh").lower()
    assert "explicitly asks" in lowered
    assert "wins over" in lowered


def test_current_message_is_anchored_to_the_separator():
    """review #335 M5: with turn-context relocation on, the last user
    message starts with an English context block — "current message" must
    point past the '--- User message ---' separator (spelled via the
    USER_MESSAGE_SEPARATOR constant, one spelling repo-wide) and be phrased
    for the separator-absent case too."""
    from xyz_agent_context.context_runtime.prompts import USER_MESSAGE_SEPARATOR

    section = build_reply_language_section("zh")
    assert USER_MESSAGE_SEPARATOR in section
    assert "is present" in section  # relocation may be off


def test_region_variant_and_unknown_code_fall_back():
    assert "Chinese" in build_reply_language_section("zh-CN")
    unknown = build_reply_language_section("tlh")
    assert "(tlh)" in unknown  # bare code still communicated
