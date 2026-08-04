"""
@file_name: test_message_content_guard.py
@date: 2026-08-04
@description: --text / --markdown values that look like file references are
rejected with an instructive error instead of being delivered literally.

Live incident (2026-08-04, claude_code x lark): the model's first
``--markdown <unquoted multi-word>`` was rejected by lark-cli, so it wrote
the reply to ``lark_reply.md`` and retried with ``--markdown
@lark_reply.md`` — an invented @file syntax these flags do not support.
lark-cli sent the literal string "@lark_reply.md" AND returned success, so
the human got garbage while the agent believed it delivered. The guard
turns that silent misdelivery into a correctable error; @-mentions and
ordinary @-containing prose must keep flowing.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.lark_module._lark_command_security import (
    sanitize_command,
)

CHAT = "oc_0822639c9c2d42a5e2589a18d1ab4d2c"


@pytest.mark.parametrize("flag", ["--text", "--markdown"])
@pytest.mark.parametrize("ref", ["@lark_reply.md", "@notes.txt", "@./out/reply.md"])
def test_file_reference_content_is_rejected(flag, ref):
    with pytest.raises(ValueError, match="do not read files"):
        sanitize_command(
            f"im +messages-send --chat-id {CHAT} {flag} {ref} --as bot"
        )


@pytest.mark.parametrize(
    "content",
    [
        "@张三 明天的会改到三点",  # mention + prose (quoted, has spaces)
        "@all 服务器已恢复",  # broadcast mention
        "@bob.smith 你好呀，请查收",  # dotted mention but with prose
        "普通消息，提到 file.md 这个文件名",  # filename inside prose
    ],
)
def test_mentions_and_prose_still_flow(content):
    args = sanitize_command(
        f'im +messages-send --chat-id {CHAT} --text "{content}" --as bot'
    )
    assert content in args


def test_bare_mention_without_extension_is_allowed():
    args = sanitize_command(
        f"im +messages-send --chat-id {CHAT} --text @all --as bot"
    )
    assert "@all" in args


def test_shell_substitution_hint_is_flag_appropriate():
    """The unexpandable-shell guard's recovery hint must not steer message
    flags toward @file — probe verified (2026-08-04) that --text/--markdown
    have NO @file expansion (a valid relative path still ships the literal
    string). Message flags get the inline hint; --content keeps @file."""
    with pytest.raises(ValueError) as e:
        sanitize_command(
            f'im +messages-send --chat-id {CHAT} --markdown "$(cat reply.md)" --as bot'
        )
    assert "inline" in str(e.value).lower()
    assert "--content @" not in str(e.value)

    with pytest.raises(ValueError) as e2:
        sanitize_command(
            'docs +update --doc-id D --content "$(cat report.md)" --as user'
        )
    assert "--content @" in str(e2.value)


def test_docs_markdown_at_file_stays_permitted():
    """Under docs the same flag carries document content — the @file
    convention there is outside this guard's scope and must keep flowing."""
    args = sanitize_command("docs +create --title T --markdown @./report.md --as user")
    assert "@./report.md" in args


def test_json_at_file_flags_are_untouched():
    """The @./file convention on --json flags is lark-cli's own feature —
    the guard only covers the literal-body message flags."""
    args = sanitize_command(
        "base +record-batch-create --base-token T --table-id t --json @./batch.json --as user"
    )
    assert "@./batch.json" in args
