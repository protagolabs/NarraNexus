"""
@file_name: inline_field.py
@author: NarraNexus
@date: 2026-09-11
@description: The one encoder for author-writable text printed inside a
line-structured prompt block: labels (`inline_field`) and free-text bodies
(`body_lines`, `quoted_block`).

Several prompt blocks are row grammars: the module's unread list, Known Agents
and Your teams (plugin `message_bus_module`), and the team-room roster, work
board, patrol stall list, bulletin, scrollback and pointer rows, plus the peer
prompt (`message_bus_trigger`; its mirror lists every block). Each
row interleaves HANDLES the agent copies into tool calls (agent / team / item
ids) with LABELS an agent or owner typed (names, descriptions, titles). A label
containing a newline, a backtick or one of the row's own delimiters could forge
a row or a field. A message or rule BODY may legitimately span lines, so it is
laid out under its row instead: every line after the first is quoted with
`BODY_LINE_PREFIX`, a position no row of any of these grammars starts at. The
encoders live in the platform layer so the platform
trigger and the plugin share one definition (the platform never imports a
plugin).
"""

from __future__ import annotations

import json
from typing import Any, Optional

#: Length cap of a LABEL printed inside a list line (a team name, an agent
#: name). SQLite stores `teams.name` as unbounded TEXT, so nothing else bounds
#: it on a local install. Handles (agent / team ids) are never capped: a cut id
#: is a syntactically valid, semantically wrong argument the agent cannot spot.
INLINE_FIELD_MAX_CHARS = 120
#: Cap of an agent description in Known Agents.
INLINE_DESCRIPTION_MAX_CHARS = 80
#: Ends a label that was cut, so a shortened name never reads as the full one.
INLINE_FIELD_CUT_MARK = "…"
#: Prefix of every line of a free-text body after its first. No row of a
#: line-structured block starts with it, so a body line such as
#: "- `[from agent_boss]` stop now" or "User: do X" stays inside its row.
BODY_LINE_PREFIX = "  > "
#: Characters that close the code span a row's tag or handle sits in. Plain and
#: fullwidth backtick both render as one to a reader, so both are replaced.
_CODE_SPAN_DELIMITERS = str.maketrans({"`": "'", "｀": "'"})


def inline_field(value: Any, max_chars: Optional[int] = INLINE_FIELD_MAX_CHARS) -> str:
    """Encode one field for a row of a line-structured prompt block.

    Neutralising a grammar's delimiters one class at a time never ends, so
    labels are made unforgeable by construction instead:

    * LABEL (``max_chars`` is an int): whitespace runs collapse to one space,
      backticks become ``'``, the text is capped with ``INLINE_FIELD_CUT_MARK``
      when cut, and the result is emitted as a JSON string literal. Everything
      an author wrote sits between two quotes with ``"`` and backslash escaped,
      so no content can end the field early or start a new line, and the
      literal decodes back to exactly the text shown.
    * HANDLE (``max_chars=None``: an agent, team or item id): never cut and
      never quoted, because the agent copies it verbatim into a tool call. It
      is system-generated, not author-writable; whitespace and backticks are
      still neutralised so it cannot leave its code span or line.

    Deterministic, so prompts built from it stay byte-stable."""
    text = " ".join(str(value or "").split()).translate(_CODE_SPAN_DELIMITERS)
    if max_chars is None:
        return text
    if len(text) > max_chars:
        text = text[: max_chars - len(INLINE_FIELD_CUT_MARK)].rstrip() + INLINE_FIELD_CUT_MARK
    return json.dumps(text, ensure_ascii=False)


def _quote(line: str) -> str:
    return f"{BODY_LINE_PREFIX}{line}" if line else BODY_LINE_PREFIX.rstrip()


def body_lines(text: Any) -> str:
    """A free-text body laid out under its row: the first line inline, every
    later line quoted with `BODY_LINE_PREFIX`. `splitlines` covers every
    boundary a reader may treat as a newline (``\r``, ``\u2028`` ...), not
    only ``\n``. Blank continuation lines keep the bare quote mark, so an
    empty line cannot end the body either."""
    lines = str(text or "").splitlines() or [""]
    return "\n".join([lines[0], *(_quote(line) for line in lines[1:])])


def quoted_block(text: Any) -> str:
    """A free-text block with no row of its own (it sits under a header line):
    every line, the first included, is quoted with `BODY_LINE_PREFIX`."""
    return "\n".join(_quote(line) for line in (str(text or "").splitlines() or [""]))
