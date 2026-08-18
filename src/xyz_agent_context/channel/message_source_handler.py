"""
@file_name: message_source_handler.py
@author: Bin Liang
@date: 2026-05-11
@description: Per-source dispatch table for chat-history processing.

Each WorkingSource value (`chat`, `lark`, `message_bus`, `job`, `a2a`,
`callback`, `skill_study`, future channels …) maps to one
MessageSourceHandler that answers two questions for the chat-history
pipeline:

  1. Write-side  — "Did the agent reply to the user this turn via this
     source's tools?" (`is_user_reply_tool(tool_name)`)
  2. Read-side   — "How should this stored row be labelled to the LLM?"
     (`format_row_prefix(msg)`)

Channels that need custom behaviour (Lark recognises `lark_cli` tools,
Matrix would recognise matrix-specific tools, etc.) register their own
handler at module-load time:

    MessageSourceRegistry.register(MessageSourceHandler(
        name="lark",
        user_reply_tool_names=(
            "notify_owner",
            "lark_cli +messages-send",
            "lark_cli +messages-reply",
        ),
        row_prefix_template="[Lark · {sender_name} in {room_name}]",
    ))

All sources that need nothing channel-specific (`chat`, `a2a`,
`callback`, `skill_study`, …) fall back to the default handler, which
recognises both owner-facing names (`reply_owner` / `notify_owner`, since
owner chat itself resolves here) and renders rows with a
"[NarraNexus UI · user=<id>]" prefix.

Why a registry instead of `if working_source == "lark": ...`
- Iron rule #3 (modules independent): chat_module / context_runtime
  must not import lark_module or message_bus.
- Iron rule #4 (generic vs scenario-specific separated): per-source
  knowledge lives with its source module, generic dispatch lives here.
- Easy to extend: a new IM trigger ships one `Registry.register(...)`
  call and zero changes elsewhere.
- Easy to debug: `MessageSourceRegistry.dump()` shows the full table.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from loguru import logger


# OpenAI Responses-API "citation" tokens that the model emits inline
# in user-facing text when WebSearch ran. Examples observed in the
# wild (2026-06-08, gpt-5.5 via codex): ``citeturn6view0``,
# ``citeturn2news12``, ``citeturn7search9``.
#
# ChatGPT's first-party frontend resolves these into clickable
# Markdown links via a separate annotation table — but the
# ``openai-codex`` Python SDK 0.1.0b3 doesn't surface that table
# (``OutputTextContentItem`` carries just ``{text, type}``). Without
# the URL/title map we can't render proper links; the pragmatic fix
# is to strip the tokens so users see clean prose instead of
# literal cryptic markers glued to sentence ends.
#
# Strip lives HERE (the single reply-extraction chokepoint shared by
# every channel) rather than in any per-framework translator,
# because:
#  * The tokens come from the model's text written into the
#    ``content`` argument of an owner-facing tool
#    (or any other reply tool) — they're plain string content, not
#    SDK-protocol metadata. Stripping at the SDK boundary would
#    miss tokens that the model writes into ``lark_cli`` markdown,
#    ``slack_cli`` markdown, ``tg_cli``, etc.
#  * Every channel funnels its reply through this method, so one
#    strip here covers all of them.
#
# Regex requires TWO alpha+digit cycles after ``cite`` to avoid
# false-matching the English word "cite" followed by a noun.
_CITE_TOKEN_RE = re.compile(r"cite[a-z]+\d+[a-z]+\d+")


def strip_responses_api_citation_tokens(text: str) -> str:
    """Public alias — same as ``_strip_responses_api_citation_tokens``,
    re-exported without the leading underscore so callers outside this
    module (notably ``response_processor`` building ProgressMessages
    for live UI streaming) can apply the same strip. Kept on the
    underscore name too for backwards compat with the internal call
    site below."""
    return _strip_responses_api_citation_tokens(text)


def _strip_responses_api_citation_tokens(text: str) -> str:
    """Remove inline citation tokens and tidy up the leftover spacing.

    Returns ``text`` unchanged if no token is present (fast path);
    otherwise strips every token and collapses doubled spaces / fixes
    spaces-before-punct that the strip introduces.
    """
    if not text or "cite" not in text:
        return text
    cleaned = _CITE_TOKEN_RE.sub("", text)
    if cleaned == text:
        return text
    # Tighten up artifacts the strip itself produced.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    # Strip whitespace that's now ahead of punctuation (Chinese +
    # English punctuation kept together so this is i18n-safe).
    cleaned = re.sub(r"\s+([。，；、,.;])", r"\1", cleaned)
    # Strip trailing horizontal whitespace on every line (the strip
    # often leaves a token-shaped hole at end-of-paragraph that the
    # ahead-of-punct rule above doesn't catch because there's no
    # punctuation after it).
    cleaned = re.sub(r"[ \t]+$", "", cleaned, flags=re.MULTILINE)
    return cleaned


ReplyExtractor = Callable[[str, Dict[str, Any]], Optional[str]]
"""Custom extractor: given (tool_name, arguments) from a ProgressMessage,
return the user-visible reply text if this tool call sent something to
the user, else None. Channels with non-standard reply tools (e.g. Lark's
`lark_cli` whose payload sits inside `command`'s `--markdown` flag) ship
their own extractor; everyone else uses the default substring + `content`
arg fallback."""


PLATFORM_REPLY_TEXT_KEY = "_platform_reply_text"
"""Argument key carrying reply text the PLATFORM wrote and delivered.

Set by ``step_3``'s IM DM no-reply fallback, which sends through
``ChannelSenderRegistry`` instead of the model calling the channel's tool.
The resulting synthetic frame therefore doesn't match the tool's real
argument shape, and every channel-specific extractor would mis-read it —
so ``extract_reply_text`` honours this key before anything else. Leading
underscore because it is ours, not part of any channel's tool schema.
"""


class _SafeFormatDict(dict):
    """A dict that returns an empty string for missing keys instead of
    raising KeyError, so a row that's missing channel_tag fields still
    renders a sensible prefix instead of crashing the whole prompt."""

    def __missing__(self, key: str) -> str:
        return ""


@dataclass(frozen=True)
class MessageSourceHandler:
    """Per-WorkingSource hook for the chat-history pipeline.

    See module docstring for the design rationale. This class only
    holds configuration + pure helpers — no I/O, no async, no DB.
    """

    name: str
    """Matches the `WorkingSource` enum value, e.g. `lark`, `chat`."""

    user_reply_tool_names: Tuple[str, ...]
    """Substrings of `tool_name` that count as the agent replying to
    the user via this source. Substring match (not equality) so MCP
    prefixes like `mcp__chat_module__notify_owner`
    match the short name registered here."""

    owner_visible_reply_tool_names: Optional[Tuple[str, ...]] = None
    """The subset of reply tools whose output SURFACES IN THE OWNER'S
    WEB CHAT. None (the default) means "same as user_reply_tool_names"
    — correct for chat and the IM channels, where the conversation IS
    with the owner. Sources whose reply target is somebody else (the
    bus: replies go to peer AGENTS) override this to just the
    owner-notify tool, so "delivered to whoever contacted you" (metrics,
    NO-REPLY judgment) and "visible to the owner" (session anchor,
    chat-history persistence) stay two separate questions — conflating
    them let every agent-to-agent bus reply re-anchor the owner's
    session (PR #230 review)."""

    display_label: str = ""
    """Human-readable name for this source, for the one line of prompt that
    tells the agent where the turn came from (`render_origin_declaration`).
    Empty falls back to `name.title()`, which is right for every brand-named
    channel and wrong only where the source name is a platform-internal word —
    those set it explicitly.

    Why the label lives HERE and not in each trigger's prose: the prose used to
    say it, each copy in its own words, and the copies drifted. One field, one
    renderer, and the same registry entry that decides which tools count as a
    reply — so the sentence and the enforcement cannot disagree."""

    row_prefix_template: str = "[{name}]"
    """str.format-style template applied to a flattened
    `{**meta_data, **channel_tag}` dict at render time. Missing keys
    substitute to "" (see _SafeFormatDict) so legacy rows without
    `channel_tag` still render without raising."""

    extract_reply_fn: Optional[ReplyExtractor] = None
    """Optional per-channel reply extractor. When set, completely
    overrides the default substring match + `arguments['content']`
    fallback. Used for channels where the reply text isn't in a
    `content` argument (Lark stuffs it into `command`'s `--markdown`
    flag, for example)."""

    dedicated_trigger: bool = False
    """True when this source has its own long-running trigger process
    (LarkTrigger, WeChatTrigger, ...) that already runs AgentRuntime for
    every inbound message. Until 2026-08-17 `ChannelInboxWriter` mirrored those
    turns into ``bus_messages`` under ``{name}_{chat_id}`` for history/Inbox
    display; the inbox has its own tables now and nothing writes them, but the
    rows survive on deployed databases. `im_channel_prefixes()` derives the
    channel-id prefixes from this flag for two consumers: MessageBusTrigger must
    NOT re-dispatch them (a second run sends duplicate replies — 2026-07-03
    wechat double-dispatch incident) and `LocalMessageBus._unread_where` must not
    inject them into agent context. Every module
    that ships a ``run_*_trigger.py`` entrypoint must set this; enforced
    by tests/message_bus/test_bus_channel_inbox_skip.py."""

    @property
    def label(self) -> str:
        """`display_label` with the derive-from-name fallback applied."""
        return self.display_label or self.name.replace("_", " ").title()

    def is_user_reply_tool(self, tool_name: str) -> bool:
        """True when `tool_name` matches any registered reply tool.

        Kept as a public helper for callers that only need the binary
        match (e.g. tests, debug tooling). The primary extraction path
        is `extract_reply_text`, which also pulls the actual content."""
        if not tool_name:
            return False
        return any(pat in tool_name for pat in self.user_reply_tool_names)

    @property
    def effective_owner_visible_names(self) -> Tuple[str, ...]:
        """The owner-visible list after the None-fallback — the single
        place the fallback rule lives, so log lines and matching can
        never drift apart."""
        if self.owner_visible_reply_tool_names is None:
            return self.user_reply_tool_names
        return self.owner_visible_reply_tool_names

    def is_owner_visible_reply_tool(self, tool_name: str) -> bool:
        """True when `tool_name`'s output surfaces in the owner's web
        chat. Falls back to the full reply list when no owner-visible
        subset is declared (chat / IM channels)."""
        if not tool_name:
            return False
        return any(pat in tool_name for pat in self.effective_owner_visible_names)

    def extract_owner_visible_text(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[str]:
        """`extract_reply_text`, gated on owner visibility. Consumers that
        decide what the OWNER saw (session anchor in step_4, the
        user-visible split in ChatModule) call this; consumers that ask
        "did the turn deliver to its origin at all" keep
        `extract_reply_text`."""
        if not self.is_owner_visible_reply_tool(tool_name):
            return None
        return self.extract_reply_text(tool_name, arguments)

    def extract_reply_text(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Optional[str]:
        """Return the user-visible reply text from this tool call.

        Three-way contract (all falsy consumers behave identically, but
        ``_classify_event`` needs the distinction):

        - non-empty ``str`` — the reply text.
        - ``""`` — the call WAS a reply attempt, but its text stripped
          down to blank (all-citation reply, literal whitespace, or a
          missing ``content`` arg). Blank after strip = no reply.
        - ``None`` — the call wasn't a user reply at all (unmatched tool
          name, or a custom extractor rejecting e.g. a lark_cli non-send
          command). Downstream may still surface it as a real tool call.

        Custom `extract_reply_fn` short-circuits this; otherwise falls
        back to substring match on `tool_name` + `arguments['content']`.

        The extracted text is run through
        ``_strip_responses_api_citation_tokens`` regardless of which
        extractor produced it — the strip is a content-layer cleanup
        that applies uniformly to every channel (chat / lark / slack
        / telegram / job). See the module-level helper docstring for
        why we strip rather than resolve.
        """
        # Platform-written frames first. A no-reply fallback reply is
        # delivered by the platform through ChannelSenderRegistry, not by
        # the model calling the channel's tool, so the frame we synthesise
        # is NOT shaped like a real tool call — and every channel-specific
        # extractor would mis-read it: wechat's reads `arguments["text"]`
        # and would fall back to its "(sent via wechat_send)" placeholder,
        # lark's parses a `command` string and would return None (making a
        # delivered reply look like silence). The text is authoritative
        # here precisely because we wrote it.
        platform_text = (arguments or {}).get(PLATFORM_REPLY_TEXT_KEY)
        if platform_text is not None:
            text = platform_text
        elif self.extract_reply_fn is not None:
            text = self.extract_reply_fn(tool_name, arguments or {})
            if text is None:
                return None
        elif self.is_user_reply_tool(tool_name):
            text = (arguments or {}).get("content", "")
        else:
            return None
        text = _strip_responses_api_citation_tokens(text or "")
        if not text.strip():
            return ""
        return text

    def format_row_prefix(self, msg: Dict[str, Any]) -> str:
        """Render the per-row prefix for `msg`.

        Pulls placeholders from `meta_data` and `meta_data.channel_tag`
        (channel_tag wins on overlap so e.g. an inner `sender_name`
        always reflects the IM sender, not a stray meta field)."""
        meta: Dict[str, Any] = msg.get("meta_data") or {}
        ctag = meta.get("channel_tag")
        flat: Dict[str, Any] = {"name": self.name, "role": msg.get("role", "")}
        flat.update({k: v for k, v in meta.items() if not isinstance(v, (dict, list))})
        if isinstance(ctag, dict):
            flat.update({k: v for k, v in ctag.items() if not isinstance(v, (dict, list))})
        return self.row_prefix_template.format_map(_SafeFormatDict(flat))


#: The owner-facing delivery tools — one destination, two registers
#: (`reply_owner` on the owner's own chat turn, `notify_owner` everywhere else;
#: see ChatModule.get_expressive_tools). Lives HERE because this module already
#: has to reason about both — the default handler lists both — and because every
#: consumer of the distinction is downstream of the registry.
#:
#: Anything asking "did this reach the owner" must accept BOTH. Anything asking
#: "is this the CHANNEL's own tool" must reject both — a filter that named only
#: one silently let the other through, which is how an IM fallback frame got
#: tagged `reply_owner` and would have surfaced in the owner's chat panel as if
#: the agent had addressed them.
_OWNER_TOOL_RE = re.compile(r"(?:reply|notify)_owner$")


def is_owner_tool(tool_name: str | None) -> bool:
    """True for `reply_owner` / `notify_owner`, bare or MCP-prefixed."""
    return bool(tool_name) and _OWNER_TOOL_RE.search(tool_name or "") is not None


_DEFAULT_HANDLER = MessageSourceHandler(
    name="default",
    # BOTH owner-facing names, and that is not belt-and-braces.
    #
    # The owner's own chat turn resolves to this handler (there is no explicit
    # "chat" registration), and its desk carries `reply_owner` — while every
    # other turn carries `notify_owner`. Listing only one of them would make
    # `_has_organic_reply` blind on the surface that uses the other: a chat turn
    # that answered perfectly would read as "never spoke", and the helper-LLM
    # fallback would write a SECOND reply on top of every successful turn.
    #
    # A source that wants the two questions separated declares
    # `owner_visible_reply_tool_names` itself, as message_bus does.
    user_reply_tool_names=("reply_owner", "notify_owner"),
    # Not "Default" — the label is read by the agent, not by us. Every source
    # that lands here (owner chat, a2a, callback, skill_study) is happening
    # inside NarraNexus, which is exactly one of the two social situations the
    # harness teaches.
    display_label="NarraNexus",
    row_prefix_template="[NarraNexus UI]",
)
"""Fallback for any WorkingSource that didn't register itself.

This covers the user-facing chat trigger, A2A, callback, skill_study,
and any future trigger that does not introduce a new reply tool.
We never need to register `chat` explicitly — the default behaviour
is exactly what `chat` needs."""


class MessageSourceRegistry:
    """Global registry. Channel-specific modules register one handler
    each at import/module-load time."""

    _handlers: Dict[str, MessageSourceHandler] = {}

    @classmethod
    def register(cls, handler: MessageSourceHandler) -> None:
        """Register `handler` against its `name`.

        Raises if the name is already taken — this is intentional;
        accidental duplicate registration would silently shadow another
        channel's reply detection, which is a class of bug we never
        want to debug at runtime."""
        if handler.name in cls._handlers:
            raise ValueError(
                f"duplicate MessageSourceHandler registration for {handler.name!r}"
            )
        cls._handlers[handler.name] = handler
        logger.info(
            f"MessageSourceRegistry: registered handler for '{handler.name}' "
            f"(reply tools={handler.user_reply_tool_names})"
        )

    @classmethod
    def get(cls, working_source: str) -> MessageSourceHandler:
        """Return the handler for `working_source`, falling back to the
        default handler when nothing is registered. Never returns None
        — callers can use the result unconditionally."""
        return cls._handlers.get(working_source, _DEFAULT_HANDLER)

    @classmethod
    def handlers(cls) -> Dict[str, MessageSourceHandler]:
        """Read-only snapshot of all registered handlers.

        Exists so MessageBusTrigger can derive the dedicated-trigger
        channel prefixes from registrations instead of a hand-maintained
        list (which drifted: wechat/narramessenger/discord were missing)."""
        return dict(cls._handlers)

    @classmethod
    def dump(cls) -> Dict[str, Dict[str, Any]]:
        """Snapshot of the registry for debug logging. JSON-serialisable
        — drops the (non-serialisable) extract_reply_fn callable, replaces
        it with a `"<custom>" if present else None` flag so we still see
        which handlers have custom extraction."""
        out: Dict[str, Dict[str, Any]] = {}
        for name, h in cls._handlers.items():
            d = asdict(h)
            d["extract_reply_fn"] = "<custom>" if h.extract_reply_fn else None
            out[name] = d
        return out


# ============================================================================
# The origin declaration (design §6.1)
# ============================================================================

ORIGIN_DECLARATION_TEMPLATE = (
    "[Origin] {label} · reply with {default_tool}{others_clause}"
)


def im_channel_prefixes() -> tuple[str, ...]:
    """Channel-id prefixes owned by dedicated IM triggers — registry-driven.

    Two consumers, and they guard different things:

    * `MessageBusTrigger` must not RE-DISPATCH these channels — their own trigger
      already ran AgentRuntime for the message.
    * `LocalMessageBus._unread_where` must not INJECT them into agent context.

    Both are about the same rows: pre-2026-08-17 IM history that the retired
    `ChannelInboxWriter` wrote into `bus_messages` under `{channel}_{chat_id}`.
    Nothing writes them any more — the inbox has its own tables — but they
    survive on every deployed database, so this is not dead code and must not be
    deleted as such under 铁律 #2. It can retire once those rows are purged; the
    runbook that purges them says so.

    The set used to be a hand-maintained tuple ("lark_", "telegram_", "slack_")
    and it silently drifted — wechat, narramessenger and discord were missing, so
    every message on those channels fired a SECOND agent run wearing the
    Owner-Relay peer-agent prompt (2026-07-03 wechat incident: fabricated
    context_token sends + bogus "我已经在微信上回复你啦" platform DMs). Deriving
    from `dedicated_trigger` keeps a future channel covered the moment it
    registers; computed per call because channel modules register at import time
    and import order is not guaranteed.

    Lives here rather than in `message_bus_trigger` because this is where the
    registry it reads lives, and because `local_bus` — a lower layer than the
    trigger — now needs it too.
    """
    return tuple(sorted(
        f"{name}_"
        for name, handler in MessageSourceRegistry.handlers().items()
        if handler.dedicated_trigger
    ))


def render_origin_declaration(
    working_source: str,
    expressive_tools: "Sequence[str]",
    reply_is_plain_text: bool = False,
) -> str:
    """One line naming where this turn came from and what answers it.

    This replaced a paragraph in every trigger prompt, each restating the
    reply rule in its own words. Those restatements are what drifted: a
    channel's copy would still describe a tool the desk no longer carried, and
    the agent had two sentences to choose between with nothing to break the
    tie.

    Both halves of this line come from data the platform already computed:

    * the label from `MessageSourceRegistry` — the same registry entry that
      decides which tool calls count as a reply from this source;
    * the tools from the turn's declared expressive surface — the SAME tuple
      `get_expressive_tools` produced and `get_disallowed_tools` enforced.

    So the sentence cannot contradict the desk: there is no second copy of
    either fact to fall out of step.

    Empty tools → empty string. A turn with no declared reply surface must not
    be handed a sentence claiming one; inventing a tool name here would be the
    exact failure the declaration exists to prevent.

    ``reply_is_plain_text`` is the other empty case, and it is not derivable
    from the tools. The line's premise is that origin-first ordering puts the
    origin module's tool at position 0 — true only while the origin module
    declares something. A patrol turn declares nothing (its reply IS the agent's
    plain text, posted by the platform), so position 0 becomes whatever ranked
    next — `notify_owner`, which the registry legitimately lists as a way to
    answer a bus turn. "reply with `notify_owner`" would then tell the lead to
    message its owner instead of writing the room's status line: the wrong act,
    and the one the patrol prompt spends a paragraph forbidding.

    The registry cannot tell these apart — `notify_owner` really is one of this
    source's reply tools — so the caller passes the fact, which the platform
    already stamps as `BUS_PLAIN_TEXT_TURN_EXTRA_KEY`. Other modules keep their
    own tools on such a turn (escalating to the owner mid-sweep is legitimate);
    what must not happen is a sentence presenting one of them as how to answer.
    """
    if reply_is_plain_text:
        return ""
    tools = tuple(expressive_tools or ())
    if not tools:
        return ""
    handler = MessageSourceRegistry.get(working_source)
    others = ", ".join(f"`{t}`" for t in tools[1:])
    return ORIGIN_DECLARATION_TEMPLATE.format(
        label=handler.label,
        default_tool=f"`{tools[0]}`",
        others_clause=f" (also available: {others})" if others else "",
    )
