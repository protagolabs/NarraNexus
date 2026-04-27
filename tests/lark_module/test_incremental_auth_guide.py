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
        "from your earlier",
        "from your previous",
        "from your prior",
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


def test_guide_distinguishes_bot_scope_recovery_from_user_scope():
    """A missing_scope on a --as bot call cannot be fixed by `auth
    login` — bot scopes are opened at the Lark developer console. A
    guide that collapses both into "mint a URL" would push bot-scope
    failures down a dead-end path the user can't redeem.
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _INCREMENTAL_AUTH_GUIDE,
    )

    lower = _INCREMENTAL_AUTH_GUIDE.lower()
    # Bot-scope branch must be mentioned — either by calling out `--as
    # bot` explicitly or by mentioning the developer console / console
    # URL as the recovery path.
    bot_branch_signals = [
        "--as bot",
        "developer console",
        "console_url",
        "bot scope",
        "bot-identity",
    ]
    assert any(s in lower for s in bot_branch_signals), (
        "Incremental auth guide must distinguish bot-scope recovery "
        "(developer console) from user-scope recovery (auth login). "
        "Collapsing both into a single path pushes bot-scope errors "
        "into a URL loop the user cannot redeem."
    )


def test_guide_mentions_scope_accumulation():
    """The agent should know that granted scopes persist across
    logins — otherwise it will pile all previously-granted scopes into
    every new login URL, forcing the user to re-authorize them each
    time.
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _INCREMENTAL_AUTH_GUIDE,
    )

    lower = _INCREMENTAL_AUTH_GUIDE.lower()
    signals = [
        "already-granted",
        "already granted",
        "carry over",
        "accumulate",
        "persist across",
    ]
    assert any(s in lower for s in signals), (
        "Incremental auth guide must indicate that scopes accumulate "
        "across logins. Without this, the agent may bundle "
        "previously-granted scopes into every new URL."
    )


def test_guide_points_agent_at_lark_shared_skill_doc():
    """Our module prompt is not a replacement for the full skill docs —
    it should carry a few critical reminders inline and otherwise point
    the agent at `lark_skill(agent_id, "lark-shared")` for the
    authoritative contract.
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _INCREMENTAL_AUTH_GUIDE,
    )

    assert "lark_skill(" in _INCREMENTAL_AUTH_GUIDE, (
        "Guide should reference `lark_skill(...)` so the agent knows "
        "where to read the full contract when inline reminders aren't "
        "enough."
    )
    assert "lark-shared" in _INCREMENTAL_AUTH_GUIDE


def test_iron_rules_include_destructive_confirmation():
    """Deleting docs, cancelling meetings, removing chat members,
    broadcasting to large groups — these are irreversible or
    high-blast-radius actions. The iron rules must require
    confirmation before executing when intent is ambiguous.
    """
    from xyz_agent_context.module.lark_module.lark_module import _IRON_RULES

    lower = _IRON_RULES.lower()
    # A rule that addresses destructive writes — accept either framing.
    assert "destructive" in lower or "irreversible" in lower, (
        "Iron rules should explicitly address destructive / irreversible "
        "actions."
    )
    assert "confirm" in lower, (
        "Iron rules should require the agent to confirm with the user "
        "before executing destructive actions when intent is ambiguous."
    )


def test_narranexus_specifics_teaches_workspace_isolation():
    """The Lark skill docs assume a global CLI install; NarraNexus
    isolates per-agent. The agent must be told that `Read`/`Glob`/`Grep`
    cannot see skill files — use `lark_skill(agent_id, name, path)`.
    Missing this, the agent will keep trying `Read` on paths that
    cross-references like `../lark-shared/SKILL.md` suggest.
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _NARRANEXUS_SPECIFICS,
    )

    assert "lark_skill(" in _NARRANEXUS_SPECIFICS, (
        "NarraNexus-specifics section must direct the agent at "
        "`lark_skill(agent_id, name, path)` for reading Lark reference "
        "material."
    )
    # Explicitly flag that filesystem read tools don't work for this.
    lower = _NARRANEXUS_SPECIFICS.lower()
    assert "read" in lower and ("glob" in lower or "grep" in lower), (
        "NarraNexus-specifics should name `Read`/`Glob`/`Grep` as the "
        "tools that CANNOT see Lark skill files, so the agent doesn't "
        "keep reaching for them."
    )


def test_narranexus_specifics_teaches_per_agent_auth():
    """Auth tokens are per-agent, not global. The agent should know
    that completing OAuth for one agent doesn't grant anything to
    another, and that `lark_setup` / `lark_bind` manage credentials
    per agent (i.e. do NOT shell out to `lark-cli config init`).
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _NARRANEXUS_SPECIFICS,
    )

    lower = _NARRANEXUS_SPECIFICS.lower()
    per_agent_signals = ["per-agent", "per agent", "each agent", "isolated"]
    assert any(s in lower for s in per_agent_signals), (
        "NarraNexus-specifics must state that auth is per-agent."
    )
    # Should point at the MCP tools that manage credentials rather
    # than letting the agent shell out to global CLI commands.
    assert "lark_setup" in _NARRANEXUS_SPECIFICS or "lark_bind" in _NARRANEXUS_SPECIFICS


def test_guide_warns_about_admin_approval_preceding_user_authorization():
    """For enterprise tenants and/or scopes the tenant hasn't pre-approved
    at the app level, the first `auth login --scope X --no-wait` URL
    clicked by the user often submits an admin-approval request, not
    personal authorization. Lark then returns
    `authorization failed: ... pending approval` on poll until the admin
    approves. Only after admin approval does a second mint + click yield
    the personal-auth token.

    Without this knowledge the Agent:
    - promises the user "click this once and I'll have your files"
    - on `pending approval` just re-mints and sends a new URL, confusing
      the user ("I just clicked, why another URL?")
    - never surfaces the two-stage nature until the user pieces it
      together themselves

    The guide must teach this up-front so the Agent sets correct
    expectations ("this may first need admin approval") and handles
    `pending approval` errors without re-minting prematurely.
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _INCREMENTAL_AUTH_GUIDE,
    )

    lower = _INCREMENTAL_AUTH_GUIDE.lower()
    # Must name the admin approval concept.
    assert "admin" in lower and "approval" in lower, (
        "Lark guide must mention admin approval as a possible first "
        "stage of incremental-scope authorization."
    )
    # Must tell the Agent NOT to promise a one-click completion when
    # the scope might need admin approval.
    one_click_signals = [
        "two clicks",
        "two different",
        "two urls",
        "not one",
        "don't promise",
        "do not promise",
        "don't guarantee",
    ]
    assert any(s in lower for s in one_click_signals), (
        "Guide must push back on the 'click once and done' framing "
        "so Agent sets correct expectations when first minting."
    )
    # Must name the poll error pattern so Agent recognizes it.
    pending_signals = [
        "pending approval",
        "pending_approval",
        "pending-approval",
    ]
    assert any(s in lower for s in pending_signals), (
        "Guide must name the `pending approval` poll error so Agent "
        "recognizes it and doesn't re-mint blindly."
    )


def test_guide_reminds_agent_to_restate_device_code_in_reasoning():
    """Key lesson from 2026-04-23 prod session with agent_7f357515e25a:
    even though the Agent correctly intended `--device-code <D>` as the
    next step, it wrote `auth login --device-code --as ...` because the
    `device_code` value lived only in the prior turn's tool_call_output
    and did not survive into this turn's context. The runtime-level fix
    persists the Agent's reasoning across turns, but only values the
    Agent *wrote into its reasoning* get carried — not raw tool outputs.

    So the Lark guide must explicitly nudge the Agent to restate the
    `device_code` (and the verification URL) in its reasoning before
    ending the mint turn. Otherwise it'll still lose the value next
    turn even with the new persistence path working.
    """
    from xyz_agent_context.module.lark_module.lark_module import (
        _INCREMENTAL_AUTH_GUIDE,
    )

    lower = _INCREMENTAL_AUTH_GUIDE.lower()
    # Must mention reasoning / thinking as the durable channel.
    assert "reasoning" in lower or "thinking" in lower, (
        "Lark guide must reference the Agent's reasoning as the place "
        "to carry the device_code across turns."
    )
    # Must name the action verb — restate / write / keep in reasoning.
    action_verbs = ["restate", "write it", "keep it", "record it", "note it", "copy it"]
    assert any(v in lower for v in action_verbs), (
        "Lark guide must use an explicit action verb so the Agent "
        "knows what to DO with the device_code, not just that it "
        "exists."
    )


def test_narranexus_specifics_rendered_only_when_stage_completed():
    """Same rationale as the other post-onboarding sections: during
    onboarding (stage != completed) the three-click flow handles
    lark-cli access end-to-end; these NarraNexus callouts are noise.
    """
    import inspect

    from xyz_agent_context.module.lark_module import lark_module as lm

    src = inspect.getsource(lm.LarkModule.get_instructions)
    assert "_NARRANEXUS_SPECIFICS" in src, (
        "get_instructions must render _NARRANEXUS_SPECIFICS so the "
        "workspace / per-agent-auth callouts actually reach the agent."
    )
