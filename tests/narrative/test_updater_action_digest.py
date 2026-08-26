"""
@file_name: test_updater_action_digest.py
@date: 2026-08-12
@description: Tests for build_action_digest (the tool-action compressor) and
updater feeds to the helper LLM (defect A1).

Before A1 the updater read only ``Event.final_output``. When an agent delivers
its answer through a channel tool, ``final_output`` degrades to a meta-comment
("I've already sent the findings"), so every topic noun the turn produced —
file names, hosts, error codes — was absent from the retrieval surface and the
narrative became unreachable by BM25 on the next turn.

Every expectation below is anchored to a measured fact from the full-database
survey (539 events / 19,790 log entries), recorded in
``reference/self_notebook/data/eventlog_survey_2026-08-12.md``. Section
references in the test docstrings point at that report.
"""

from datetime import datetime, timezone
from typing import Any, List

import pytest

from xyz_agent_context.narrative._narrative_impl import updater as updater_mod
from xyz_agent_context.narrative._narrative_impl.updater import (
    ACTION_DIGEST_BUDGET,
    build_action_digest,
)
from xyz_agent_context.narrative.models import (
    Event,
    EventLogEntry,
    Narrative,
    NarrativeInfo,
    NarrativeType,
    TriggerType,
)

NOW = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)


def _entry(entry_type: str, content: Any) -> EventLogEntry:
    return EventLogEntry(timestamp=NOW, type=entry_type, content=content)


def _thinking(text: str) -> EventLogEntry:
    # Survey §3: the real shape is {content, display, type}.
    return _entry("thinking", {"content": text, "display": text, "type": "thinking"})


def _call(tool_name: str, arguments: dict) -> EventLogEntry:
    # Survey §3: arguments is a dict in 1550/1550 entries — never a JSON string.
    return _entry(
        "tool_call",
        {
            "tool_name": tool_name,
            "arguments": arguments,
            "tool_call_id": "call_1",
            "type": "tool_call",
        },
    )


def _output(text: str) -> EventLogEntry:
    return _entry("tool_output", {"output": text, "type": "tool_output"})


def _event(event_log: List[EventLogEntry], final_output: str = "done") -> Event:
    return Event(
        id="evt_test",
        trigger=TriggerType.CHAT,
        trigger_source="user_test",
        env_context={"input": "test input", "timestamp": NOW.isoformat()},
        module_instances=[],
        event_log=event_log,
        final_output=final_output,
        created_at=NOW,
        updated_at=NOW,
        agent_id="agent_test",
    )


def _narrative() -> Narrative:
    return Narrative(
        id="nar_test",
        type=NarrativeType.CHAT,
        agent_id="agent_test",
        narrative_info=NarrativeInfo(
            name="Deploy script errors",
            description="",
            current_summary="",
            actors=[],
        ),
        event_ids=["evt_test"],
        topic_keywords=["deploy"],
        created_at=NOW,
        updated_at=NOW,
    )


# ---------------------------------------------------------------------------
# The four RED assertions named in the defect doc §3.8
# ---------------------------------------------------------------------------

def test_tool_names_appear_in_digest():
    """Tool names are the topic nouns the retrieval surface is missing."""
    digest = build_action_digest([
        _call("Glob", {"pattern": "**/*deploy*"}),
        _output("No files found"),
        _call("Read", {"file_path": "/tmp/project/.evermemos/web.log"}),
        _output("[Errno 48] Address already in use"),
        _call("Bash", {"command": "ls -la deploy/"}),
        _output("deploy.sh restart.sh"),
    ])

    assert "Glob" in digest
    assert "Read" in digest
    assert "Bash" in digest
    assert "web.log" in digest


def test_thinking_entries_are_dropped():
    """Survey F2: thinking is 82.7% of entries / 55.1% of characters, and is
    process, not topic identity."""
    digest = build_action_digest([
        _thinking("Let me look for the deployment script somewhere on disk"),
        _thinking("The user probably means the EC2 one"),
        _call("Glob", {"pattern": "**/*deploy*"}),
        _output("found"),
    ])

    assert "Glob" in digest
    assert "somewhere on disk" not in digest
    assert "EC2" not in digest


def test_over_budget_truncates_and_marks_the_omission():
    """Binding rule: never swallow information silently. Over budget we keep the
    most recent steps and say how many were dropped (survey §6.3)."""
    log: List[EventLogEntry] = []
    for i in range(120):
        log.append(_call("Bash", {"command": f"echo step-{i:03d} " + "x" * 100}))
        log.append(_output("ok"))

    digest = build_action_digest(log)

    assert len(digest) <= ACTION_DIGEST_BUDGET
    assert "earlier steps omitted" in digest
    # Reverse retention: the newest step survives, the oldest is the one dropped.
    assert "step-119" in digest
    assert "step-000" not in digest


def test_empty_event_log_produces_no_section():
    """Survey F10: 40.1% of events have no tool_call at all. Those turns must
    not get an empty heading."""
    assert build_action_digest([]) == ""
    assert build_action_digest([_thinking("just answering from memory")]) == ""


# ---------------------------------------------------------------------------
# Survey-derived rules the defect doc could not know before the data existed
# ---------------------------------------------------------------------------

def test_credentials_are_redacted():
    """Survey F5: the local database holds real Lark app_secrets and Slack /
    Telegram bot tokens inside tool_call arguments. Un-redacted they would land
    in current_summary / topic_keywords, persist to the narratives row, and be
    injected into every later system prompt."""
    digest = build_action_digest([
        _call("mcp__lark_module__lark_bind", {
            "app_id": "cli_a1b2c3d4",
            "app_secret": "o0dhSECRETVALUEmustNOTleak123RoZ",
        }),
        _output('{"success": true}'),
        _call("mcp__slack_module__slack_bind", {
            "bot_token": "xoxb-99887766-SECRETmustNOTleak-abcdeIW",
        }),
        _output('{"success": true}'),
    ])

    assert "o0dhSECRETVALUEmustNOTleak123RoZ" not in digest
    assert "xoxb-99887766-SECRETmustNOTleak-abcdeIW" not in digest
    assert "<redacted>" in digest
    # The action itself still reaches the retrieval surface.
    assert "lark_bind" in digest
    assert "cli_a1b2c3d4" in digest


def test_token_shaped_value_under_an_innocuous_key_is_redacted():
    """Second layer of F5: a key-name denylist alone does not stop a token
    pasted into a Bash command line."""
    digest = build_action_digest([
        _call("Bash", {
            "command": 'curl -H "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz012345" https://api.example.com',
        }),
        _output("200"),
    ])

    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in digest


def test_identifier_and_control_arguments_are_dropped():
    """Survey F6: 45.9% of argument instances are IDs / control flags. They are
    short (agent_id is 18 chars), so no length threshold can filter them — they
    have to go by key name."""
    digest = build_action_digest([
        _call("mcp__chat_module__send_message_to_user_directly", {
            "agent_id": "agent_25dec880a426",
            "user_id": "user_tc",
            "content": "The deploy script is at deploy.sh",
        }),
        _output('{"success": true}'),
    ])

    assert "agent_25dec880a426" not in digest
    assert "user_tc" not in digest
    assert "deploy.sh" in digest


def test_path_values_are_truncated_from_the_head_so_the_basename_survives():
    """Survey F7: the topic noun of a path lives at its tail. Head-truncating an
    81-char file_path is exactly how `deploy.sh` got lost."""
    long_path = "/Users/tc/Library/Application Support/narranexus-desktop/project/deploy/deploy.sh"
    assert len(long_path) > 80  # guards the premise of this test

    digest = build_action_digest([
        _call("Read", {"file_path": long_path}),
        _output("#!/bin/bash"),
    ])

    assert "deploy.sh" in digest


def test_delivered_message_body_survives_far_enough_to_carry_its_nouns():
    """Survey F8: in the reference event the nouns `Errno 48` / `端口` sit at
    offsets 555-606 of the delivered body. A 200-char cap loses them; the body
    budget has to reach past them."""
    body = (
        "找到了与部署相关的脚本和日志，汇总如下：\n"
        + "详情" * 250          # pushes the payload past offset 500
        + "\n[Errno 48] 端口 1995 被占用"
    )
    digest = build_action_digest([
        _call("mcp__chat_module__send_message_to_user_directly", {
            "agent_id": "agent_test",
            "user_id": "user_tc",
            "content": body,
        }),
        _output('{"success": true}'),
    ])

    assert "Errno 48" in digest
    assert "端口" in digest


def test_tool_output_contributes_status_only_not_text():
    """Survey F3: tool_output is 32.4% of all characters, and its topic nouns sit
    at offsets 738-7070 — unreachable by any sane head slice. Keep the
    success/failure signal (a failed deploy is a different topic state than a
    successful one), drop the body."""
    digest = build_action_digest([
        _call("Bash", {"command": "bash deploy.sh"}),
        _output("Traceback (most recent call last):\n  File deploy.py line 3\nPermissionError: nope"),
        _call("Bash", {"command": "ls"}),
        _output("a.txt b.txt c.txt"),
    ])

    assert "PermissionError" not in digest
    assert "a.txt" not in digest
    assert "error" in digest.lower()


def test_mcp_module_prefix_is_stripped():
    """`mcp__lark_module__lark_send_message` is not a noun; `lark_send_message`
    is. The prefix is pure plumbing repeated on 57 distinct tools."""
    digest = build_action_digest([
        _call("mcp__lark_module__lark_send_message", {"text": "hi"}),
        _output("ok"),
    ])

    assert "lark_send_message" in digest
    assert "mcp__" not in digest


def test_agent_final_output_entry_is_not_duplicated():
    """final_output already has its own section in the update context."""
    digest = build_action_digest([
        _call("Glob", {"pattern": "*.sh"}),
        _output("deploy.sh"),
        _entry("agent_final_output", {
            "content": "UNIQUEFINALMARKER already sent",
            "length": 30,
            "type": "agent_final_output",
        }),
    ])

    assert "UNIQUEFINALMARKER" not in digest


# ---------------------------------------------------------------------------
# Integration with the context the updater actually sends
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_update_context_never_includes_the_actions_section():
    """C3 (PR2_CROWDING_ANALYSIS, 2026-08-26): digest content must never
    reach the updater context. The updater's output IS the continuity
    anchor — continuity reads the same four fields (name / description /
    summary / keywords) to describe "the thread you are in" every turn, so
    feeding tool actions here made the updater RENAME threads after their
    most recent tool call ("团队群聊自我介绍" became "深圳天气查询") and
    continuity then judged the user's normal follow-up as a topic switch:
    same-line rate 65.3% -> 56.8% on anchor-rewritten rows (McNemar
    p=0.0002), a net 24 correct continuations broken per exam, the broken
    turns adopted by OTHER existing lines (54/55). The digest machinery
    itself stays (see build_action_digest's docstring for its designated
    step-2 consumer); this pin is about the one entry point that poisons
    the anchor.
    """
    updater = updater_mod.NarrativeUpdater("agent_test")
    event = _event(
        [
            _thinking("looking around"),
            _call("Read", {"file_path": "/tmp/project/.evermemos/web.log"}),
            _output("[Errno 48] Address already in use"),
        ],
        final_output="Good — I've already sent the findings.",
    )

    context = await updater._build_update_context(_narrative(), event)

    assert "Actions taken this turn" not in context
    assert "web.log" not in context
    # The rest of the context is untouched by the removal.
    assert "Agent Response: Good" in context


def test_nested_credentials_inside_a_dict_argument_are_redacted():
    """Third layer of F5 (independent review 2026-08-21, Critical #1): the
    key-name check used to see only TOP-LEVEL argument keys. A dict value —
    and `args` is the most common nested-dict carrier, with the generous
    800-char body cap — serialized straight through `json.dumps`, so
    `{"args": {"app_secret": ...}}` sailed past both regexes: the key check
    never saw the nested name, and a Lark app_secret is a prefix-less
    alphanumeric string the value regex cannot recognise. This was newly
    reachable in this branch: before event_log sync, the digest was always
    built from [].
    """
    digest = build_action_digest([
        _call("mcp__lark_module__lark_bind", {
            "args": {
                "app_id": "cli_a1b2c3d4",
                "app_secret": "o0dhNESTEDsecretMUSTnotLEAK99RoZ",
            },
        }),
        _output('{"success": true}'),
    ])

    assert "o0dhNESTEDsecretMUSTnotLEAK99RoZ" not in digest
    assert "<redacted>" in digest


def test_prose_mentioning_the_word_token_is_not_redacted():
    """The guard for the fix above: the kv-shaped text check must not eat a
    plain string VALUE that merely mentions a secret-ish word. Losing this
    turn's nouns is exactly the recall gap the digest exists to close."""
    digest = build_action_digest([
        _call("mcp__chat_module__send_message", {
            "content": "帮我查一下这个月的 token 用量和 password 策略",
        }),
        _output('{"success": true}'),
    ])

    assert "token 用量" in digest
    assert "password 策略" in digest
    assert "<redacted>" not in digest


def test_kv_secret_inside_a_plain_string_value_is_redacted():
    """Fourth layer of F5 (PR #361 review round 2, I1): the kv-shaped check
    was gated on isinstance(dict|list), so a STRING value carrying
    `LARK_APP_SECRET=...` — under an innocuous key like `command`, with a
    prefix-less value the shape regex cannot recognise — passed all three
    layers. Same blood path as C1, different container."""
    digest = build_action_digest([
        _call("Bash", {
            "command": "export LARK_APP_SECRET=o0dhSTRINGsecretMUSTnotLEAKRoZ && ./deploy.sh",
        }),
        _output("ok"),
        _call("mcp__lark_module__lark_call", {
            "body": '{"app_id": "cli_x", "app_secret": "o0dhJSONstringMUSTnotLEAK9RoZ"}',
        }),
        _output('{"success": true}'),
    ])

    assert "o0dhSTRINGsecretMUSTnotLEAKRoZ" not in digest
    assert "o0dhJSONstringMUSTnotLEAK9RoZ" not in digest
    assert "<redacted>" in digest
