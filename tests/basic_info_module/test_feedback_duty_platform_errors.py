"""
@file_name: test_feedback_duty_platform_errors.py
@date: 2026-09-09
@description: Regression guard for the "Product Feedback Duty" section of the
BasicInfo system-prompt template, the submit_feedback tool description, and the
tool's once-per-agent-per-code dedup gate.

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

Trigger 3 is machine-generated — a platform-wide outage reproduces on every
tool call — so "file it once" is enforced in ``_basic_info_mcp_tools`` rather
than left to the model's memory; the dedup tests pin that gate, including the
release-on-failed-delivery rule that keeps the cache from holding a failure
sentinel.
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
    # Slice to the NEXT `#### ` heading, not to the first ` --- `: a horizontal
    # rule added inside this (still-growing) section would silently shrink the
    # slice and leave every guard below reading only its first half.
    start = text.index("#### Product Feedback Duty")
    return text[start: text.index("#### ", start + len("#### Product Feedback Duty"))]


def _feedback_tool() -> tuple[str, object]:
    """Return (tool description, the registered submit_feedback coroutine)."""
    from narranexus_plugins.basic_info_module import _basic_info_mcp_tools as mt

    captured: dict = {}

    class _Mcp:
        def tool(self, **kw):
            def _wrap(fn):
                captured[kw["name"]] = (kw["description"], fn)
                return fn
            return _wrap

    mt._register_feedback_tool(_Mcp())
    return captured["submit_feedback"]


def _tool_description() -> str:
    return _feedback_tool()[0]


@pytest.fixture
def clean_dedup():
    from narranexus_plugins.basic_info_module import _basic_info_mcp_tools as mt

    mt._feedback_dedup.clear()
    yield mt
    mt._feedback_dedup.clear()


# ── Prompt template ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_the_duty_section_slice_reaches_its_last_rule(text):
    # Guards the guard: every assertion below is scoped to this slice, and the
    # last rule in the section (the no-paste red line) is its most load-bearing.
    duty = _duty_section(text)
    assert duty.rstrip().rstrip("-").rstrip().endswith("not to compare two of them.")


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_rejected_platform_credential_is_a_feedback_trigger(text):
    duty = _duty_section(text)
    third = duty[duty.index("3. "):duty.index("Rules:")]
    assert "agent-token-invalid" in third
    assert "category `error`" in third
    # Fires even when a workaround later succeeds, once per tool + code —
    # enforced by the tool via dedup_key, not by the model's memory.
    assert "workaround" in third
    assert "dedup_key" in third
    assert "ONCE per agent per tool + code" in third


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
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


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_trigger_three_wins_when_trigger_two_also_fires(text):
    # A platform credential failing the same instruction twice satisfies both;
    # without a stated precedence the same incident lands in two categories.
    duty = _duty_section(text)
    third = duty[duty.index("3. "):duty.index("Rules:")]
    assert "also satisfies trigger 2" in third
    assert "trigger 3 only" in third


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_the_tool_name_and_error_code_are_not_covered_by_never_include_names(text):
    # "never include names" means PII; ten lines earlier the same section
    # REQUIRES the tool name and the error code. Without the carve-out a
    # cautious model files "a platform credential was rejected" — useless for
    # triage, which is the entire value of trigger 3.
    duty = _duty_section(text)
    rules = duty[duty.index("Rules:"):duty.index("Be conservative")]
    assert "personal names" in rules
    assert "The TOOL name and the error code are required" in rules
    assert "they are not secrets" in rules


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_no_diagnosis_rule_is_scoped_to_platform_injected_credentials(text):
    duty = _duty_section(text)
    para = duty[duty.index("Be conservative"):]
    assert "do NOT assert a diagnosis" in para
    assert "MIGHT mean" in para
    # The carve-out: user-provided secrets and BYOK keys keep concrete guidance.
    assert "BYOK" in para
    assert "applies to credentials the user provided" in para


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_announcing_the_notification_is_an_explicit_trigger_three_exception(text):
    # The Rules paragraph says "don't announce that you filed feedback"; the
    # trigger-3 paragraph says to tell the user the team was notified. Those
    # collide unless the exception is written down as one, so pin the wording
    # that scopes it — and pin that the general rule survives for 1 and 2.
    duty = _duty_section(text)
    rules = duty[duty.index("Rules:"):duty.index("Be conservative")]
    assert "don't announce that you filed feedback unless the user asked" in rules

    para = duty[duty.index("Be conservative"):]
    assert "For trigger-3 errors ONLY" in para
    assert "unlike the general rule above" in para
    assert "team has been notified" in para
    assert "for triggers 1 and 2 the general rule still holds" in para


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_prompt_forbids_pasting_credentials(text):
    para = _duty_section(text)
    para = para[para.index("Be conservative"):]
    assert "never paste a token" in para
    assert "credential file" in para


# ── Tool description ────────────────────────────────────────────────────────

def test_tool_description_names_the_platform_error_trigger():
    desc = _tool_description()
    assert "(c)" in desc
    assert "`agent-token-invalid`" in desc
    assert "category `error`" in desc
    assert "Not (c)" in desc and "`no_credential`" in desc
    assert "`official-agent-required`" in desc


def test_tool_description_tells_the_agent_to_pass_a_dedup_key():
    desc = _tool_description()
    assert 'dedup_key="<tool>:<code>"' in desc
    assert "ONCE per agent per tool+code" in desc
    # The agent must not be asked to remember what it filed.
    assert "never have to remember" in desc


def test_tool_description_states_the_b_vs_c_precedence():
    assert "satisfies both (b) and (c)" in _tool_description()


def test_dedup_key_is_optional_so_triggers_a_and_b_keep_working():
    import inspect

    sig = inspect.signature(_feedback_tool()[1])
    assert sig.parameters["dedup_key"].default == ""


# ── Dedup gate ──────────────────────────────────────────────────────────────

async def _call(mt, monkeypatch, fn, *, delivered=True, **kw):
    sent: list[dict] = []

    async def _fake_send(**payload):
        sent.append(payload)
        return delivered

    monkeypatch.setattr(
        "narranexus.platform.integrations.feedback_client.send_feedback", _fake_send
    )
    result = await fn(
        agent_id=kw.pop("agent_id", "agent_a"),
        user_id="user_1",
        category="error",
        summary="narra_cli rejected a platform token",
        **kw,
    )
    return result, sent


@pytest.mark.asyncio
async def test_a_delivered_report_suppresses_later_repeats_of_the_same_code(
    clean_dedup, monkeypatch
):
    fn = _feedback_tool()[1]
    first, sent = await _call(clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")
    assert first["ok"] is True and len(sent) == 1

    second, sent2 = await _call(clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")
    # Still ok=True — a failure return would make the agent retry or apologise.
    assert second["ok"] is True
    assert "Already filed" in second["message"]
    assert sent2 == []


@pytest.mark.asyncio
async def test_dedup_is_per_agent_and_per_code(clean_dedup, monkeypatch):
    fn = _feedback_tool()[1]
    await _call(clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")

    _, other_agent = await _call(
        clean_dedup, monkeypatch, fn,
        dedup_key="narra_cli:agent-token-invalid", agent_id="agent_b",
    )
    assert len(other_agent) == 1

    _, other_code = await _call(
        clean_dedup, monkeypatch, fn, dedup_key="narra_cli:no_endpoint",
    )
    assert len(other_code) == 1


@pytest.mark.asyncio
async def test_no_dedup_key_means_no_dedup(clean_dedup, monkeypatch):
    # Triggers (a)/(b) call without the key; two complaints are two reports.
    fn = _feedback_tool()[1]
    _, first = await _call(clean_dedup, monkeypatch, fn)
    _, second = await _call(clean_dedup, monkeypatch, fn)
    assert len(first) == 1 and len(second) == 1
    assert not clean_dedup._feedback_dedup


@pytest.mark.asyncio
async def test_a_failed_delivery_does_not_consume_the_one_allowed_report(
    clean_dedup, monkeypatch
):
    # The cache must not hold a failure sentinel: if the intake was down, the
    # platform error has NOT been reported and the next call must try again.
    fn = _feedback_tool()[1]
    _, first = await _call(
        clean_dedup, monkeypatch, fn,
        delivered=False, dedup_key="narra_cli:agent-token-invalid",
    )
    assert len(first) == 1
    assert not clean_dedup._feedback_dedup

    _, second = await _call(
        clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid",
    )
    assert len(second) == 1


@pytest.mark.asyncio
async def test_expired_entries_stop_suppressing_and_are_swept(clean_dedup, monkeypatch):
    mt = clean_dedup
    fn = _feedback_tool()[1]
    await _call(mt, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")

    # Age every entry past the TTL by rewriting its stored stamp — the clock
    # itself is left alone so nothing else in the process is affected.
    for slot in list(mt._feedback_dedup):
        mt._feedback_dedup[slot] -= mt.FEEDBACK_DEDUP_TTL_SECONDS + 1
    stale = mt._dedup_slot("agent_zzz", "gone:stale")
    mt._feedback_dedup[stale] = mt.time.monotonic() - mt.FEEDBACK_DEDUP_TTL_SECONDS - 1

    _, sent = await _call(mt, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")
    assert len(sent) == 1
    # …and the unrelated expired entry was swept rather than left to accumulate.
    assert stale not in mt._feedback_dedup


def test_the_cache_is_bounded_in_entries_and_key_length(clean_dedup):
    mt = clean_dedup
    for i in range(mt.FEEDBACK_DEDUP_MAX_ENTRIES + 50):
        mt._dedup_reserve(mt._dedup_slot("agent_a", f"tool:{i}"))
    assert len(mt._feedback_dedup) == mt.FEEDBACK_DEDUP_MAX_ENTRIES

    # dedup_key is caller-controlled text; it must not be stored unbounded.
    slot = mt._dedup_slot("agent_a", "x" * 10_000)
    assert len(slot[1]) == mt.FEEDBACK_DEDUP_KEY_MAXLEN
