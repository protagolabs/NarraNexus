"""
@file_name: test_incremental_auth_guide.py
@author: Bin Liang
@date: 2026-04-23
@description: Prompt-content regression guard for the incremental scope
authorization guidance.

Context: on 2026-04-22 production user xinyao_test_v1 ran into a Lark
`missing_scope: space:document:retrieve` error and the agent looped 6
times within 13 minutes, each time minting a fresh `auth login --scope X
--json --no-wait` URL and sending the new verification URL to her, never
polling the `device_code` from the prior mint. Root cause: the
`_IDENTITY_GUIDE` prompt and the `lark_cli` tool docstring only taught
the `--no-wait` mint half of the flow; neither taught the follow-up
`auth login --device-code D` poll on the next turn, nor the "do not
re-mint while a URL is in flight" discipline.

These tests pin the two-step, two-turn discipline into the prompt text
so future edits cannot silently regress the guidance. They are
intentionally structural (substring / phrase presence); LLM behavioural
quality is validated end-to-end in prod, not in CI.
"""
from __future__ import annotations


def test_guide_teaches_both_no_wait_and_device_code_sides():
    """The guide must mention `--no-wait` (mint side) and `--device-code`
    (poll side). Failure here means the agent is being taught only half
    the flow — which is exactly the bug that trapped xinyao_test_v1.
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _INCREMENTAL_AUTH_GUIDE,
    )

    assert "--no-wait" in _INCREMENTAL_AUTH_GUIDE
    assert "--device-code" in _INCREMENTAL_AUTH_GUIDE


def test_guide_teaches_two_turn_boundary():
    """The agent must be told to stop after sending the URL and wait for
    the user's next message, not poll inside the same turn. Pre-fix the
    agent would poll `--device-code` ~4 seconds after `--no-wait` (see
    agent_c9af2f03afec logs 2026-04-22 20:42:19 → 20:42:23), get
    `authorization_pending`, and conclude the device_code was broken.
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _INCREMENTAL_AUTH_GUIDE,
    )

    lower = _INCREMENTAL_AUTH_GUIDE.lower()
    # At least one of these phrasings must appear to establish the
    # "wait for the user's next message before polling" rule.
    signals = [
        "next turn",
        "next message",
        "same turn",
        "this turn",
        "end your turn",
        "end the turn",
    ]
    assert any(s in lower for s in signals), (
        "Incremental auth guide must teach the two-turn boundary — "
        "mint and send in one turn, poll in a later turn. Absent any of "
        f"{signals!r} in the guide, the agent may poll too early and "
        "conclude the device_code is broken."
    )


def test_guide_forbids_re_minting_while_url_in_flight():
    """The agent must be told NOT to mint a fresh URL when a recent
    `--no-wait` is still outstanding for the same scope. Pre-fix this
    rule was absent and the agent re-minted on every turn (6 URLs in
    13 minutes for xinyao).
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _INCREMENTAL_AUTH_GUIDE,
    )

    lower = _INCREMENTAL_AUTH_GUIDE.lower()
    signals = [
        "do not mint",
        "don't mint",
        "not mint a new",
        "do not re-mint",
        "don't re-mint",
        "not re-mint",
        "do not issue a new",
        "don't issue a new",
    ]
    assert any(s in lower for s in signals), (
        "Incremental auth guide must explicitly forbid minting a new URL "
        "when one is already in flight for the scope. Missing this rule "
        "is what let the agent loop 6 times for xinyao_test_v1."
    )


def test_guide_teaches_remembering_device_code_from_prior_turn():
    """Step 2 must tell the agent to use the device_code from the prior
    `--no-wait` response, not to mint again. This ties the two turns
    together.
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _INCREMENTAL_AUTH_GUIDE,
    )

    lower = _INCREMENTAL_AUTH_GUIDE.lower()
    signals = [
        "device_code from",
        "device_code returned",
        "the device_code you got",
        "previous --no-wait",
        "prior --no-wait",
        "earlier --no-wait",
        "step 1",
    ]
    assert any(s in lower for s in signals), (
        "Incremental auth guide must explicitly reference the device_code "
        "from the previous turn's --no-wait response, not a freshly minted "
        "one. Otherwise the agent won't connect the two turns."
    )


def test_guide_is_rendered_only_when_stage_completed():
    """During onboarding (stage != completed) the three-click flow
    handles auth entirely; incremental top-up guidance would only
    confuse the agent. Confirm the module gates the guide the same way
    it already gates _IDENTITY_GUIDE.
    """
    # We verify the gating by reading the source text of get_instructions
    # rather than rendering it — rendering requires a ctx_data fixture
    # with lark_info, which is overkill for a prompt-presence test.
    import inspect

    from xyz_agent_context.module.lark_module import lark_module as lm

    src = inspect.getsource(lm.LarkModule.get_instructions)
    # The guide constant must appear in the render function's body, and
    # must be gated on stage == "completed" (same pattern as the
    # existing _IDENTITY_GUIDE gate).
    assert "_INCREMENTAL_AUTH_GUIDE" in src, (
        "get_instructions must reference _INCREMENTAL_AUTH_GUIDE so the "
        "guide actually reaches the agent's system prompt."
    )
    assert 'stage == "completed"' in src
