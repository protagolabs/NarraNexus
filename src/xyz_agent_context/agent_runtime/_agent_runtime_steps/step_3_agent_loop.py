"""
@file_name: step_3_agent_loop.py
@author: NetMind.AI
@date: 2025-12-22
@description: Step 3 - Narrative Smart Agent Loop (CASE1: AGENT_LOOP)

Build context and run Agent Loop (implicit Module orchestration).
This is the processing path for complex tasks, requiring LLM implicit orchestration within the Agent Loop.
"""

from __future__ import annotations

import json
import os
import re
from typing import AsyncGenerator, Any, Union, TYPE_CHECKING

from loguru import logger
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.schema import (
    AgentTextDelta,
    AgentThinking,
    ProgressMessage,
    ProgressStatus,
    PathExecutionResult,
    ErrorMessage,
    AUTH_EXPIRED_ERROR_TYPE,
    SELF_SERVICEABLE_ERROR_TYPE,
    EXECUTOR_INFRA_ERROR_TYPE,
)
from xyz_agent_context.context_runtime import ContextRuntime

# Top-level on purpose. These three channel modules depend on stdlib +
# loguru only and never import agent_runtime, so there is no cycle to dodge
# (channel_trigger_base depends on this layer, not the other way round, and
# it already uses a lazy import for that direction). They were function-local
# in six places, including inside the step generator body where the import
# ran once per turn.
from xyz_agent_context.channel.channel_prompts import ROOM_TYPE_DIRECT
from xyz_agent_context.channel.channel_sender_registry import (
    ChannelSenderRegistry,
)
from xyz_agent_context.channel.message_source_handler import (
    PLATFORM_REPLY_TEXT_KEY,
    MessageSourceRegistry,
)
from xyz_agent_context.agent_framework import get_agent_loop_driver
from xyz_agent_context.agent_framework.loop.turn_input import TurnInput
from xyz_agent_context.agent_framework.llm.failure import (
    SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND,
    classify_self_serviceable,
    self_serviceable_user_message,
    classify_executor_infra_failure,
    executor_infra_user_message,
)
from xyz_agent_context.agent_runtime.execution_state import ExecutionState

if TYPE_CHECKING:
    from .context import RunContext


# Default size caps for the fallback-prompt serializer. Tuned for
# helper_llm context budget — 32 KB total leaves room for the system
# prompt block + chat history. 4 KB per entry stops a single oversized
# tool result from dominating.
_DEFAULT_MAX_PER_ENTRY = 4096
_DEFAULT_MAX_TOTAL = 32768
_DROPPED_PREFIX_MARKER = "[... earlier activity omitted to fit context budget ...]\n"
_EMPTY_RESPONSE_SENTINEL = "(no activity recorded)"


def _dispatch_identity_token(ensured, user_id) -> str | None:
    """The identity token to stamp into this turn's MCP headers, or None.

    Blueprint P1: cloud = the broker minted one at ensure() time (fresh per
    run, ExecutorEnsureResult.identity_token); local = this process
    self-signs — but ONLY when NX_MCP_AUTH_MODE != off, so a default local
    run performs no keygen and no filesystem writes (iron rule #7).

    In cloud the broker is the ONLY signer — a broker without a signing key
    (or predating the field) means NOTHING is stamped, never a local
    self-sign: the mcp verifier holds the deploy-mounted public key, so a
    process-local ephemeral signature could not verify anyway, and stamping
    one would pollute the audit window's central measurement ("which callers
    are still tokenless?") with `invalid` noise — and under enforce it would
    401 every tool call (PR #260 review, Important #1).
    """
    if ensured is not None and ensured.identity_token:
        return ensured.identity_token
    if not user_id:
        return None
    from xyz_agent_context.utils.deployment_mode import is_cloud_mode

    if ensured is not None or is_cloud_mode():
        return None
    from xyz_agent_context.module.identity.mcp_auth import auth_mode

    if auth_mode() == "off":
        return None
    from xyz_agent_context.module.identity.tokens import get_local_issuer

    return get_local_issuer().token_for(user_id)


def _framework_override_viable(
    framework: str, *, claude: Any = None, codex: Any = None
) -> bool:
    """Can THIS turn's provider config actually serve the override?

    Mirrors NexusAgent._build_request_payload's two hard-fail conditions
    (OAuth subscription credentials; no model on either protocol slot)
    so a fast-mode override never bricks a turn that would have worked
    on the slot framework (review finding: no-fallback override). Other
    framework names pass through — this is a viability check, not
    policy (binding rule #15).

    ``claude``/``codex`` default to the ambient per-task configs; tests
    inject fakes instead of mutating the shared proxies.
    """
    if framework != "nexus_power":
        return True
    if claude is None or codex is None:
        from xyz_agent_context.agent_framework.api_config import (
            claude_config,
            codex_config,
        )

        claude = claude if claude is not None else claude_config
        codex = codex if codex is not None else codex_config
    # Mirror _resolve_provider's claude-FIRST short circuit, not just its
    # conditions: a non-empty claude.model means codex is never consulted,
    # so an oauth claude slot is non-viable even if codex carries a model.
    if claude.model:
        return (claude.auth_type or "api_key") not in ("oauth", "oauth_token")
    return bool(codex.model)


async def _resolve_agent_framework_name(agent_id: str, db_client: Any) -> str:
    """Return the coding-agent framework name for THIS agent (for the driver
    registry).

    Resolution mirrors the config resolver's overlay so framework and config
    never disagree:

      1. Per-agent override — ``agent_slots[agent_id, 'agent'].agent_framework``,
         but ONLY when that override actually rebinds the agent slot (has a
         ``provider_id``). A framework-only stub with no provider does NOT win,
         because the config resolver
         (``resolver._apply_agent_overrides``) skips empty-provider rows and
         would fall back to the owner default — honouring the stub framework
         here would run e.g. the Codex driver against a Claude config.
      2. Owner default — ``user_slots[owner, 'agent'].agent_framework`` where
         ``owner = agents.created_by``.

    Keyed by ``agent_id`` (NOT the trigger's ``user_id``): the owner bills and
    configures the run, so the framework must resolve from the owner + this
    agent's override — the same identity the config resolver uses. (Reading the
    trigger identity was a latent bug for background triggers.)

    Always falls back to ``"claude_code"`` on missing row / null column / DB
    lookup error — never let an ``agent_framework`` issue block an agent run.
    Unknown framework names are NOT silently rewritten here — they're handed to
    ``get_agent_loop_driver`` which raises ``ValueError`` so a config typo
    surfaces at the dispatch site instead of masquerading as "claude".

    The overlay itself lives in ``agent_framework.providers.model_identity`` — the
    SINGLE source of truth shared with the prompt's "LLM Model" line, so the
    displayed identity can never disagree with the driver we dispatch. This
    thin wrapper just projects the ``framework`` field (identity resolution
    never raises; unknown names pass through verbatim on the ``framework``
    field, so the registry still fails loud on typos).
    """
    from xyz_agent_context.agent_framework.providers.model_identity import (
        resolve_agent_model_identity,
    )

    return (await resolve_agent_model_identity(agent_id, db_client)).framework


# NOTE (2026-07-29): the in-process concurrent-resume guard, the four-fold
# handle validation and `_resolve_resume_session_id` used to live here. The
# claude adapter now authors the CLI transcript itself every turn with a
# fresh session id, so there is no stored handle to validate, nothing that
# can go stale, and no shared handle two runs could both claim — which is
# what the lease existed to prevent. See adapters/claude/transcript.py.

def _truncate(text: str, limit: int) -> str:
    """Tail-truncate ``text`` to ``limit`` bytes, appending a clear
    marker so the LLM knows content was dropped."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return text[:limit] + f"\n[truncated {dropped} bytes]"


def _render_entry(msg: Any, max_per_entry: int) -> str | None:
    """Render one runtime frame as a single labelled string, or return
    ``None`` if the frame carries nothing worth showing the fallback
    LLM (e.g. structural progress messages with no tool/result payload).
    """
    if isinstance(msg, AgentTextDelta):
        return f"[assistant_text] {msg.delta}"
    if isinstance(msg, AgentThinking):
        return _truncate(
            f"[thinking] {msg.thinking_content}", max_per_entry
        )
    if isinstance(msg, ErrorMessage):
        body = f"[error] {msg.error_type}: {msg.error_message}"
        if msg.severity != "fatal":
            body += f" (severity={msg.severity})"
        return _truncate(body, max_per_entry)
    if isinstance(msg, ProgressMessage):
        details = msg.details or {}
        tool_name = details.get("tool_name")
        if tool_name and msg.status == ProgressStatus.RUNNING:
            args = details.get("arguments", {})
            try:
                args_json = json.dumps(args, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                args_json = repr(args)
            return _truncate(
                f"[tool_call] {tool_name}({args_json})", max_per_entry
            )
        if "output" in details and msg.status == ProgressStatus.COMPLETED:
            return _truncate(
                f"[tool_output] {details.get('output', '')}", max_per_entry
            )
    return None


def _serialize_agent_loop_for_prompt(
    agent_loop_response: list,
    *,
    max_per_entry: int = _DEFAULT_MAX_PER_ENTRY,
    max_total: int = _DEFAULT_MAX_TOTAL,
) -> str:
    """Render an ``agent_loop_response`` list into a flat plain-text
    block for the fallback LLM prompt.

    Why this exists separately from the streaming/persistence paths:
    the fallback LLM needs a compact, ordered snapshot of "what
    happened this turn so far" so it can write a recovery reply that
    references the work the agent actually completed. The live stream
    is too noisy (every text delta is its own frame); the persisted
    form (chat_module) is too lossy (only the final assistant message
    survives).

    Contract:
      - Frames render in their original order (causal sequence matters).
      - Each entry is capped at ``max_per_entry`` bytes; truncation
        gets a ``[truncated N bytes]`` marker.
      - Total output is capped at ``max_total`` bytes; if exceeded,
        oldest entries drop FIRST (with a ``[... earlier activity
        omitted ...]`` marker prepended) — recent activity is what the
        recovery reply needs.
      - Adjacent ``AgentTextDelta`` frames are concatenated into one
        ``[assistant_text]`` block (matching how the frontend renders
        them) so the LLM sees coherent text instead of a delta soup.
      - Frames with no useful payload (structural ProgressMessages
        with neither tool_name nor output) are silently dropped.
    """
    if not agent_loop_response:
        return _EMPTY_RESPONSE_SENTINEL

    # Pass 1: coalesce adjacent AgentTextDelta into single entries so
    # one delta stream renders as one [assistant_text] block.
    coalesced: list[Any] = []
    buffer: list[str] = []
    for msg in agent_loop_response:
        if isinstance(msg, AgentTextDelta):
            buffer.append(msg.delta)
            continue
        if buffer:
            coalesced.append(AgentTextDelta(delta="".join(buffer)))
            buffer = []
        coalesced.append(msg)
    if buffer:
        coalesced.append(AgentTextDelta(delta="".join(buffer)))

    # Pass 2: render each entry (or drop if nothing meaningful).
    rendered: list[str] = []
    for msg in coalesced:
        line = _render_entry(msg, max_per_entry)
        if line is not None:
            rendered.append(line)

    if not rendered:
        return _EMPTY_RESPONSE_SENTINEL

    # Pass 3: enforce total cap by dropping oldest entries first.
    # Compute total length including newline separators.
    def _join(entries: list[str]) -> str:
        return "\n".join(entries)

    dropped_any = False
    while rendered and len(_join(rendered)) > max_total:
        rendered.pop(0)
        dropped_any = True

    body = _join(rendered) if rendered else ""
    if dropped_any:
        body = _DROPPED_PREFIX_MARKER + body
    return body


def _channel_turn_envelope(ctx) -> dict:
    """Read the channel turn envelope the trigger forwarded, if any.

    Shape (all optional, see ``ChannelContextBuilderBase.turn_envelope``
    and ``ChannelTriggerBase``): ``channel_room_type``,
    ``channel_reply_kwargs``, ``channel_tag``.

    Returns ``{}`` for every non-channel turn (chat / job / bus), which is
    what makes "no envelope → no DM fallback" the default. Never raises:
    a malformed envelope must not break the turn's recovery phase.
    """
    ctx_data = getattr(ctx, "ctx_data", None)
    extra = getattr(ctx_data, "extra_data", None) if ctx_data else None
    if not isinstance(extra, dict):
        return {}
    return {
        "channel_room_type": extra.get("channel_room_type", ""),
        "channel_reply_kwargs": extra.get("channel_reply_kwargs") or {},
        "channel_tag": extra.get("channel_tag") or {},
    }


# Working sources that never get a platform-written fallback reply, even
# in a 1:1 conversation. `message_bus` must not answer peer agents (that
# is the agent-to-agent loop the bus deliberately avoids); `job` has no
# channel recipient waiting on the other end.
_NO_FALLBACK_WORKING_SOURCES = frozenset({"message_bus", "job"})


def _has_organic_reply(
    agent_loop_response: list,
    working_source: str,
) -> bool:
    """True if the agent already sent a real user reply this turn.

    "A reply" is per-channel: on chat that means
    ``send_message_to_user_directly``, on an IM turn it also means the
    channel's own send tool (``wechat_send``, ``lark_cli +messages-send``,
    …). The authority is ``MessageSourceRegistry``, the same registry
    ``chat_module._delivered_to_origin`` consults — so "did this turn
    speak?" cannot drift between the two layers.

    Two consumers:

    - severity when a LATER failure hits: a turn that already spoke must
      not be re-surfaced as a hard "retry" fatal (the user got their
      answer), so it becomes ``recovered_after_reply``.
    - **the IM DM fallback's double-send guard.** This used to match
      ``send_message_to_user_directly`` only, which meant a WeChat turn
      that correctly called ``wechat_send`` read as "no reply" — turning
      the fallback below into a second, helper-written message on every
      successful turn.

    ``working_source`` is REQUIRED — there is deliberately no default. A
    ``"chat"`` default silently reintroduced the drift this function
    exists to remove: the severity call site below kept it, so an IM turn
    that had already replied via ``wechat_send`` / ``lark_cli`` and then
    hit an executor-infra failure read as "never spoke" and was recorded
    as a hard ``fatal`` (had_fatal_error) instead of
    ``recovered_after_reply`` — a delivered conversation filed as a failed
    turn, with a "retry" badge in front of the user.
    """
    handler = MessageSourceRegistry.get(working_source)
    for r in agent_loop_response:
        if not isinstance(r, ProgressMessage) or not r.details:
            continue
        details = r.details if isinstance(r.details, dict) else {}
        tool_name = details.get("tool_name") or ""
        if handler.is_user_reply_tool(tool_name):
            return True
    return False


def _should_run_helper_llm_fallback(
    working_source: str,
    agent_loop_response: list,
    cancellation,
    is_direct_message: bool = False,
) -> tuple[str | None, str]:
    """Decide what the chat fallback path should do this turn.

    Returns ``(mode, skip_reason)``:

    - ``("no_reply", "")``: chat turn finished cleanly without
      ``send_message_to_user_directly`` — run helper_llm to write the
      reply the agent forgot to send. No error to surface.
    - ``("after_error", "")``: chat turn hit a fatal mid-stream AND no
      organic reply was sent yet — run helper_llm with full context
      (system prompts + completed tool results + error info) so the
      recovered reply tells the user what was achieved, what failed,
      and a useful next step. Surface the original error as
      severity=``recovered`` after the recovery stream completes.
    - ``("partial_reply_then_error", "")``: chat turn hit a fatal AFTER
      the agent already sent a real reply — do NOT invoke helper_llm
      (the user already heard from the agent), but surface the
      truncated execution via severity=``recovered_after_reply`` so
      the badge tells the user the turn didn't finish all planned work.
    - ``("no_reply_im_dm", "")``: a **1:1 IM DM** finished cleanly and
      the agent never called the channel's reply tool. helper_llm writes
      the reply and the platform delivers it through the channel's
      registered sender (see ``_deliver_im_fallback_reply``). Added
      2026-08-06 for the 0802 WeChat report.

    - ``(None, reason)``: nothing to do. Reasons:
        * ``"non_chat_trigger"``: out of scope — ``message_bus``
          deliberately stays quiet (agent-to-agent loops) and ``job``
          has no human waiting on a channel.
        * ``"group_room_may_stay_silent"``: an IM group room. Silence is
          the designed behaviour there — see the group Communication
          Protocol.
        * ``"cancellation_requested"``: user pressed stop; honour it.
          Don't burn helper_llm tokens recovering from rejected work.
        * ``"already_replied_via_tool"``: agent did its job — clean
          loop + organic reply, nothing to recover.
        * ``"fatal_no_invented_reply"``: an IM DM turn that died
          mid-stream. Chat can afford ``after_error`` recovery because
          the frontend also shows the error badge next to it; an IM
          recipient would get a confident-sounding message with no error
          surface at all, so we stay silent rather than deliver a reply
          synthesised from half a thought.

    **Why IM DMs are in scope now.** The original gate (2026-05-12) was
    ``working_source != "chat" → out of scope``, reasoned as "job/lark
    have their own reply tooling". That conflated *having* a reply tool
    with the reply *happening*: when the model emits plain text and never
    calls the tool, the text is dropped, the turn persists as an activity
    row, and the person on the other end gets nothing. ``message_bus``
    stays excluded — that half of the 2026-05-12 reasoning is still true.

    Pulled out of the generator body so each case is exercisable by
    pure unit tests without spinning up the full async generator.
    """
    is_chat = working_source == "chat"
    is_im_dm = False
    if not is_chat:
        # "Is this an IM channel at all?" is the handler's
        # `dedicated_trigger` flag — the same signal MessageBusTrigger
        # uses to know which sources run their own AgentRuntime. It
        # separates a real channel (lark / wechat / telegram …) from
        # `callback` / `a2a` / `skill_study`, which have no room and no
        # human recipient, so "group room" would be a nonsense reason.
        handler = MessageSourceRegistry.get(working_source)
        is_channel = (
            bool(getattr(handler, "dedicated_trigger", False))
            and working_source not in _NO_FALLBACK_WORKING_SOURCES
        )
        if not is_channel:
            return None, "non_chat_trigger"
        if not is_direct_message:
            return None, "group_room_may_stay_silent"
        is_im_dm = True

    if cancellation is not None and getattr(cancellation, "is_cancelled", False):
        return None, "cancellation_requested"

    has_fatal = any(
        isinstance(r, ErrorMessage) and getattr(r, "severity", "fatal") == "fatal"
        for r in agent_loop_response
    )
    has_reply = _has_organic_reply(agent_loop_response, working_source)

    if is_im_dm:
        if has_reply:
            return None, "already_replied_via_tool"
        if has_fatal:
            return None, "fatal_no_invented_reply"
        return "no_reply_im_dm", ""

    if has_fatal and has_reply:
        return "partial_reply_then_error", ""
    if has_fatal:
        return "after_error", ""
    if has_reply:
        return None, "already_replied_via_tool"
    return "no_reply", ""


def _fallback_skip_decision(
    agent_loop_response: list, captured_error: dict | None
) -> tuple[str | None, str | None, str | None]:
    """Decide whether the helper-LLM fallback must be SKIPPED because the turn
    failed a way a fabricated reply would MASK — either a user-fixable failure
    (dead credentials, or a deterministic self-serviceable config error —
    context window too small, no credits, bad model id) OR a platform-side
    executor-infra failure (OOM kill, executor/broker unreachable). Fabricating
    a reply over such a turn hides the real cause and misleads the user (the
    "black box" P1 + incident 2026-06-11).

    Returns ``(kind, reason, target_error_type)``:
      - ``("inline", None, None)`` — response_processor already surfaced the
        fatal, actionable ErrorMessage (``auth_expired`` / ``config_actionable``);
        it's in ``agent_loop_response``. Caller skips the fallback; no new
        message needed.
      - ``("raw_exception", reason, target_error_type)`` — the loop raised a
        Python exception, so ``captured_error`` is set but NO ErrorMessage
        exists yet, and it is either self-serviceable
        (``target_error_type == SELF_SERVICEABLE_ERROR_TYPE``) or executor-infra
        (``target_error_type == EXECUTOR_INFRA_ERROR_TYPE``). Caller skips the
        fallback AND emits a fatal, actionable ErrorMessage (else invisible).
      - ``(None, None, None)`` — no maskable failure; run the normal fallback.
    """
    inline_fatal_user_fixable = any(
        isinstance(m, ErrorMessage)
        and getattr(m, "error_type", "")
        in (AUTH_EXPIRED_ERROR_TYPE, SELF_SERVICEABLE_ERROR_TYPE)
        for m in agent_loop_response
    )
    if inline_fatal_user_fixable:
        return "inline", None, None
    if captured_error is not None:
        et = captured_error.get("error_type")
        em = captured_error.get("error_message")
        # Executor-infra checked first: an unreachable-executor exception is a
        # RuntimeError whose text could otherwise be scanned by the more
        # permissive self-serviceable markers; the typed/returncode signal is
        # unambiguous, so it wins.
        infra_reason = classify_executor_infra_failure(et, em)
        if infra_reason is not None:
            return "raw_exception", infra_reason, EXECUTOR_INFRA_ERROR_TYPE
        reason = classify_self_serviceable(et, em)
        if reason is not None:
            return "raw_exception", reason, SELF_SERVICEABLE_ERROR_TYPE
    return None, None, None


NO_REPLY_NEEDED_SENTINEL = "<<<NO_REPLY_NEEDED>>>"
"""What the helper emits when the turn genuinely warranted silence.

Only the IM DM fallback honours it (see `_FALLBACK_IM_DM_EXTRA`). The DM
Communication Protocol keeps one narrow carve-out — the incoming message is
pure acknowledgment ("好的" / "谢谢" / "got it" / "👍") with nothing to add —
and without an exit the platform would answer every one of those anyway,
making the protocol's own exemption unreachable in production: the decision
to run this fallback looks only at whether a reply tool was called, and a
model that correctly stayed silent called none. Prompt and behaviour have to
agree.
"""


_FALLBACK_IM_DM_EXTRA = (
    "\n\nThis is a 1:1 IM conversation, so one more rule overrides the "
    "others when it applies: if the person's latest message is PURE "
    "ACKNOWLEDGMENT with nothing left to act on — \"好的\", \"谢谢\", "
    "\"收到\", \"got it\", \"thanks\", \"👍\" — and there is genuinely "
    f"nothing to add, reply with exactly {NO_REPLY_NEEDED_SENTINEL} and "
    "nothing else. That is the ONLY case for it. A greeting, a question, a "
    "request, or small talk all still get a real answer."
)


_FALLBACK_NO_REPLY_INSTRUCTIONS = (
    "You are the agent's voice. The agent finished thinking but never "
    "called its reply tool, so its reasoning was never spoken to the "
    "user. Produce the single message it should have sent."
    "\n\nRules:\n"
    "- Reply in the user's language (match `<current_user_message>`).\n"
    "- Address the user directly, in first person as the agent.\n"
    "- Do NOT mention tools, reply tools, helper_llm, this fallback path, "
    "or any internal state.\n"
    # The 2026-07-29 report: the agent's reasoning was pure intent ("let me
    # try the image again"), the fallback voiced it, and the user was left
    # waiting for a document nothing was producing. The turn ENDS when this
    # message is sent, so a promise here can never come true.
    "- Never promise or imply work in progress or about to start (\"I'll "
    "do X\", \"let me try Y\", \"working on it\", \"one moment\"). This "
    "turn ends the moment your message is sent, so nothing continues "
    "afterwards. Describe only what already happened.\n"
    "- If `<this_turn_activity>` shows the agent produced only intent and "
    "no actual result, say plainly that it did not get the work done, and "
    "give the user one concrete way forward (re-send, narrow the request, "
    "supply what was missing). An honest \"this didn't happen\" is always "
    "better than a confident-sounding reply about work that does not exist.\n"
    "- Keep it natural, useful, and proportional to the question."
)


_FALLBACK_AFTER_ERROR_INSTRUCTIONS = (
    "You are the agent continuing the same turn. The agent was working "
    "on the user's request, completed some steps successfully, then a "
    "step failed and the turn cannot finish as planned. Your job: tell "
    "the user what was achieved, what couldn't be done, and a useful "
    "next step they can try."
    "\n\nRules:\n"
    "- Reply in the user's language (match `<current_user_message>`).\n"
    "- Speak in first person, as the agent. Never break character with "
    "phrases like \"the system failed\" or \"an error occurred "
    "internally\". Phrase the failure operationally: \"I tried to X "
    "but couldn't reach Y\" / \"I got partway through Z\".\n"
    "- Use the `<this_turn_activity>` to be concrete about what you "
    "found in the steps that did succeed.\n"
    "- Suggest a concrete next step: rephrasing, splitting the request, "
    "or noting a temporary limitation if the error is clearly transient "
    "(rate limit / timeout).\n"
    "- Do NOT mention tool names, raw error type strings, helper_llm, "
    "or fallback paths. Translate technical errors into operational "
    "language.\n"
    "- Keep it short — one paragraph plus optional next-step bullet."
)


def _fallback_instructions_for_mode(mode: str) -> str:
    """The helper-LLM system prompt for a fallback ``mode``.

    A named seam rather than an inline conditional: these two prompts are the
    only text the platform itself puts in the user's mouth, so their contract
    (no promises about work that isn't happening — see the no_reply rules) is
    worth pinning in tests without constructing a stream.
    """
    if mode == "after_error":
        return _FALLBACK_AFTER_ERROR_INSTRUCTIONS
    if mode == "no_reply_im_dm":
        # Same no-reply text, plus the one exit the DM protocol promises.
        return _FALLBACK_NO_REPLY_INSTRUCTIONS + _FALLBACK_IM_DM_EXTRA
    return _FALLBACK_NO_REPLY_INSTRUCTIONS


def _build_helper_user_input(
    *,
    mode: str,
    context_messages: list[dict],
    agent_loop_response: list,
    final_output: str,
    user_input: str,
    error_info: dict | None,
) -> str:
    """Construct the user-input payload fed to the helper_llm for the
    fallback reply.

    Strategy: don't replay context_messages verbatim into helper_llm —
    re-instantiating the agent persona + every tool instruction risks
    helper_llm trying to "tool-call" via text. Instead extract the
    system prompts as background, render history as a transcript, and
    render this-turn-so-far via the dedicated serializer.
    """
    sections: list[str] = []

    system_blocks = [
        str(m.get("content", "")).strip()
        for m in context_messages
        if isinstance(m, dict) and m.get("role") == "system"
    ]
    system_blocks = [s for s in system_blocks if s]
    if system_blocks:
        sections.append(
            "<original_system_instructions>\n"
            + "\n\n".join(system_blocks)
            + "\n</original_system_instructions>"
        )

    history_msgs = [
        m for m in context_messages
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        # Native-replay rows can be calls-only (content None) and tool
        # rows carry role "tool" — neither belongs in a prose transcript
        # (str(None) would literally render "[assistant] None").
        and str(m.get("content") or "").strip()
    ]
    # Drop the trailing user message if it duplicates `user_input` (the
    # current turn's user input is shown verbatim in its own section).
    if (
        history_msgs
        and history_msgs[-1].get("role") == "user"
        and str(history_msgs[-1].get("content", "")) == user_input
    ):
        history_msgs = history_msgs[:-1]
    if history_msgs:
        rendered = "\n".join(
            f"[{m['role']}] {str(m.get('content', '')).strip()}"
            for m in history_msgs
        )
        sections.append(
            "<conversation_history>\n"
            + rendered
            + "\n</conversation_history>"
        )

    sections.append(
        "<current_user_message>\n"
        + user_input
        + "\n</current_user_message>"
    )

    this_turn = _serialize_agent_loop_for_prompt(agent_loop_response)
    sections.append(
        "<this_turn_activity>\n"
        + this_turn
        + "\n</this_turn_activity>"
    )

    if final_output and mode == "no_reply":
        # In no_reply mode the agent's final reasoning IS the seed for
        # the missing reply. In after_error mode the reasoning may be a
        # half-thought (loop crashed mid-stream); rely on tool results
        # in <this_turn_activity> instead.
        sections.append(
            "<agent_final_reasoning>\n"
            + final_output
            + "\n</agent_final_reasoning>"
        )

    if mode == "after_error" and error_info:
        sections.append(
            "<execution_error>\n"
            f"The turn was interrupted by:\n"
            f"  type: {error_info.get('error_type', 'unknown')}\n"
            f"  message: {error_info.get('error_message', '')}\n"
            "</execution_error>"
        )

    if mode == "after_error":
        sections.append(
            "Write a single reply to the user that names what was "
            "achieved (concrete details from <this_turn_activity>), "
            "what couldn't be done (translating <execution_error> into "
            "operational language), and a useful next step."
        )
    else:
        sections.append(
            "Write the single reply the agent should send to the user."
        )

    return "\n\n".join(sections)


async def _generate_fallback_reply_stream(
    *,
    mode: str,
    context_messages: list[dict],
    agent_loop_response: list,
    final_output: str,
    user_input: str,
    error_info: dict | None,
    db,
    agent_id: str,
):
    """Stream a helper_llm reply for the recovery slot. Yields str
    deltas.

    Modes:
      - ``"no_reply"``: agent finished cleanly but never called
        send_message_to_user_directly; produce the reply it forgot to
        send.
      - ``"after_error"``: agent loop crashed mid-stream; produce a
        recovery reply telling the user what was achieved, what
        failed, and a useful next step.

    Wrapped in its own function for two reasons:
    1. Keeps the helper_llm import + cost-context setup out of the main
       agent-loop generator body.
    2. Lets us test the prompt assembly + streaming wiring in isolation.
    """
    from xyz_agent_context.agent_framework.llm.helper_sdk import get_helper_sdk
    from xyz_agent_context.utils.cost_tracker import set_cost_context, clear_cost_context

    set_cost_context(agent_id, db)
    try:
        sdk = get_helper_sdk()
        instructions = _fallback_instructions_for_mode(mode)
        user_input_for_helper = _build_helper_user_input(
            mode=mode,
            context_messages=context_messages,
            agent_loop_response=agent_loop_response,
            final_output=final_output,
            user_input=user_input,
            error_info=error_info,
        )
        async for delta in sdk.llm_stream(
            instructions=instructions,
            user_input=user_input_for_helper,
        ):
            yield delta
    finally:
        clear_cost_context()


async def _stream_fallback_recovery(
    *,
    fallback_mode: str | None,
    captured_error: dict | None,
    context_messages: list[dict],
    agent_loop_response: list,
    final_output: str,
    user_input: str,
    cancellation,
    db,
    agent_id: str,
    working_source: str = "chat",
    channel_tag: dict | None = None,
    reply_kwargs: dict | None = None,
):
    """Drive the post-agent-loop recovery phase, yielding the messages
    the frontend should see in causal order.

    Yields (when applicable, strictly in this order):
      1. ``AgentTextDelta`` frames from the helper_llm stream.
      2. A synthetic ``ProgressMessage`` (one) tagging the fallback as
         a ``send_message_to_user_directly`` call so downstream
         persistence (chat_module) records it as a normal turn. Carries
         ``details.reply_via=helper_llm_{mode}``.
      3. An ``ErrorMessage`` (one) if ``captured_error`` was set,
         with severity computed from outcome:
           - ``recovered`` — fallback produced non-empty content;
           - ``recovered_after_reply`` — partial_reply_then_error mode
             (helper_llm did not run, agent already spoke);
           - ``fatal`` — fallback produced nothing and we have no
             organic reply either.

    Why the ErrorMessage comes LAST: the frontend reduces
    ``responseParts`` from synthetic tool calls and falls back to
    ``currentErrors`` only when no responseParts exist. If we yielded
    ErrorMessage first, ``displayContent`` would briefly flip to the
    error string before the synthetic send_message lands — half a
    second of "system broke" UX even when we recovered cleanly.

    The caller is responsible for appending each yielded message to
    its own ``agent_loop_response`` (existing convention so downstream
    hooks see the full turn).
    """
    fallback_full = ""

    if fallback_mode == "no_reply_im_dm":
        # 1:1 IM DM: the agent produced no channel reply, so the platform
        # writes one and DELIVERS it. Two deliberate differences from the
        # chat path above:
        #
        # - **no AgentTextDelta frames.** Those render in the OWNER's chat
        #   panel; this reply is addressed to the IM sender, and painting
        #   it into the owner's conversation would fake a message the
        #   agent never sent them.
        # - **the synthetic frame is tagged with the CHANNEL's send tool**,
        #   not send_message_to_user_directly, so
        #   `_split_user_visible_response` files it as an IM reply and
        #   `_delivered_to_origin` reports the turn as delivered.
        #
        # The frame is emitted ONLY after the channel confirms the send.
        # Recording "replied" for a message that never left the process is
        # the same class of lie as the discarded plain text we are fixing.
        text = ""
        try:
            chunks: list[str] = []
            async for delta_text in _generate_fallback_reply_stream(
                mode=fallback_mode,
                context_messages=context_messages,
                agent_loop_response=agent_loop_response,
                final_output=final_output,
                user_input=user_input,
                error_info=None,
                db=db,
                agent_id=agent_id,
            ):
                if (
                    cancellation is not None
                    and getattr(cancellation, "is_cancelled", False)
                ):
                    logger.info(
                        "[FALLBACK-IM] cancellation requested mid-stream; "
                        "aborting helper_llm."
                    )
                    chunks = []
                    break
                chunks.append(delta_text)
            text = "".join(chunks).strip()
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[FALLBACK-IM] helper_llm stream failed: {e}")

        # The DM protocol's one silence carve-out, honoured. Without this
        # exit the platform would answer even a bare "谢谢", because the
        # decision to get here only asks whether a reply tool was called —
        # and a model that correctly stayed silent called none.
        #
        # STRIPPED OUT rather than compared for equality. The helper runs on
        # whichever provider the user configured (binding rule #15 — we do
        # not police that choice), so quoting the sentinel, adding a full
        # stop, or prefacing it with a sentence are all within range. An
        # equality test misses every one of those and delivers the literal
        # `<<<NO_REPLY_NEEDED>>>` into someone's IM thread — a worse look
        # than the extra message this carve-out exists to avoid. Removing it
        # unconditionally means the marker can never reach a person, whether
        # it arrived alone or embedded in prose; if real text remains, that
        # text is delivered and the sentinel is simply gone.
        if NO_REPLY_NEEDED_SENTINEL in text:
            text = text.replace(NO_REPLY_NEEDED_SENTINEL, "").strip()
            # What is left after removing the marker may be punctuation the
            # model wrapped it in — `"…"`, a trailing `。` — which is not a
            # reply, just residue. Delivering that is barely better than
            # delivering the marker. `\w` treats CJK as word characters, so
            # this asks "is there any actual content here" rather than
            # enumerating quote and punctuation forms.
            if not re.search(r"\w", text):
                text = ""
            if text:
                logger.warning(
                    "[FALLBACK-IM] helper mixed the no-reply marker into a "
                    "real reply; stripped the marker and delivering the rest"
                )
            else:
                logger.info(
                    "[FALLBACK-IM] helper judged the turn needs no reply "
                    "(pure acknowledgment); staying silent"
                )

        if not text:
            logger.warning(
                "[FALLBACK-IM] no content recovered; leaving the turn silent "
                f"(working_source={working_source})"
            )
        else:
            delivered = await _deliver_im_fallback_reply(
                working_source, channel_tag or {}, reply_kwargs or {}, text
            )
            if delivered:
                fallback_full = text
                yield ProgressMessage(
                    step="3.4.fallback",
                    title="Reply (helper_llm no_reply_im_dm)",
                    description=(
                        "helper_llm wrote the reply the agent never sent, and "
                        "the platform delivered it on the channel."
                    ),
                    status=ProgressStatus.COMPLETED,
                    details={
                        "tool_name": _im_reply_tool_name(working_source),
                        # `content` for the generic reader; PLATFORM_REPLY_TEXT_KEY
                        # so every channel-specific extractor reads the real
                        # text instead of mis-parsing a frame that was never
                        # shaped like its tool call (wechat's would fall back
                        # to a "(sent via …)" placeholder, lark's would see
                        # no `command` and report silence).
                        "arguments": {
                            "content": text,
                            PLATFORM_REPLY_TEXT_KEY: text,
                        },
                        "reply_via": "helper_llm_no_reply_im_dm",
                    },
                )

    elif fallback_mode in ("no_reply", "after_error"):
        chunks: list[str] = []
        fallback_error: Exception | None = None
        try:
            async for delta_text in _generate_fallback_reply_stream(
                mode=fallback_mode,
                context_messages=context_messages,
                agent_loop_response=agent_loop_response,
                final_output=final_output,
                user_input=user_input,
                error_info=captured_error,
                db=db,
                agent_id=agent_id,
            ):
                if (
                    cancellation is not None
                    and getattr(cancellation, "is_cancelled", False)
                ):
                    logger.info(
                        "[FALLBACK] cancellation requested mid-stream; "
                        f"aborting helper_llm ({fallback_mode})."
                    )
                    break
                chunks.append(delta_text)
                yield AgentTextDelta(delta=delta_text)
        except Exception as e:  # noqa: BLE001
            fallback_error = e
            logger.exception(
                f"[FALLBACK] helper_llm ({fallback_mode}) stream failed: {e}"
            )

        fallback_full = "".join(chunks).strip()
        if fallback_full:
            synth_details: dict = {
                "tool_name": "mcp__chat_module__send_message_to_user_directly",
                "arguments": {"content": fallback_full},
                "reply_via": f"helper_llm_{fallback_mode}",
            }
            if fallback_error is not None:
                synth_details["fallback_partial"] = True
                synth_details["fallback_error"] = type(fallback_error).__name__
            yield ProgressMessage(
                step="3.4.fallback",
                title=f"Reply (helper_llm {fallback_mode})",
                description=(
                    f"helper_llm generated a reply via {fallback_mode}"
                    + (" (partial — stream errored)" if fallback_error else ".")
                ),
                status=ProgressStatus.COMPLETED,
                details=synth_details,
            )
            logger.warning(
                f"[FALLBACK] persisted reply mode={fallback_mode} "
                f"(len={len(fallback_full)} chars, "
                f"partial={fallback_error is not None})"
            )
        else:
            logger.warning(
                f"[FALLBACK] no content recovered mode={fallback_mode} "
                f"(error={fallback_error!r})"
            )

    if captured_error is not None:
        if fallback_mode == "partial_reply_then_error":
            severity = "recovered_after_reply"
        elif fallback_full:
            severity = "recovered"
        else:
            severity = "fatal"
        yield ErrorMessage(
            error_message=(
                f"Agent execution error: {captured_error.get('error_message', '')}"
            ),
            error_type=captured_error.get("error_type", "Exception"),
            severity=severity,
        )


def _im_reply_tool_name(working_source: str) -> str:
    """The channel's own send tool, for tagging a platform-delivered reply.

    Deliberately NOT ``send_message_to_user_directly``: that tool means
    "tell the owner", and tagging an IM reply with it would surface the
    message in the owner's chat panel as if the agent had addressed them
    (``chat_module._split_user_visible_response`` routes on exactly this
    distinction). Falls back to a synthetic name so the frame is still
    self-describing if a channel ever registers only the owner tool.
    """
    handler = MessageSourceRegistry.get(working_source)
    for name in handler.user_reply_tool_names:  # noqa: SIM110 — first non-owner wins
        if "send_message_to_user_directly" not in name:
            return name
    return f"{working_source}_send"


async def _deliver_im_fallback_reply(
    working_source: str,
    channel_tag: dict,
    reply_kwargs: dict,
    text: str,
) -> bool:
    """Send a platform-written fallback reply to the IM sender.

    The chat path needs no equivalent: streaming the deltas to the
    frontend IS delivery there. An IM recipient has no such stream — if
    nobody calls the channel's send API, the reply exists only in our
    database, which is exactly the 0802 failure.

    Routes through ``ChannelSenderRegistry``, which every
    ``ChannelModuleBase`` subclass populates at init, so this stays
    channel-agnostic (iron rule #3 — no channel-module import here).

    Returns True only when the channel reported success. Never raises: a
    send failure must leave the turn's own record intact, and the caller
    logs + skips the synthetic frame so we never persist "replied" for a
    message that never left the building.
    """
    channel = (channel_tag or {}).get("channel", "") or working_source
    target_id = (channel_tag or {}).get("room_id", "") or ""
    if not target_id:
        logger.warning(
            f"[FALLBACK-IM] no target id for channel={channel}; cannot deliver"
        )
        return False

    sender = ChannelSenderRegistry.get_sender(channel)
    if sender is None:
        logger.warning(
            f"[FALLBACK-IM] no sender registered for channel={channel}; "
            f"available={ChannelSenderRegistry.available_channels()}"
        )
        return False

    agent_id = (channel_tag or {}).get("agent_id", "") or ""
    try:
        result = await sender(agent_id, target_id, text, **(reply_kwargs or {}))
    except Exception as e:  # noqa: BLE001 — delivery must not kill the turn
        logger.exception(
            f"[FALLBACK-IM] send raised for channel={channel} "
            f"target={target_id}: {type(e).__name__}: {e}"
        )
        return False

    ok = bool((result or {}).get("success"))
    if ok:
        logger.warning(
            f"[FALLBACK-IM] delivered platform-written reply channel={channel} "
            f"target={target_id} len={len(text)}"
        )
    else:
        logger.warning(
            f"[FALLBACK-IM] channel refused the send channel={channel} "
            f"target={target_id} error={(result or {}).get('error')!r}"
        )
    return ok


async def _record_executor_infra_event(
    db_client: Any,
    user_id: str,
    error_type: str,
    error_str: str,
    output_already_emitted: bool,
) -> None:
    """Record an audit row when the agent loop died from an executor-infra
    failure — an OOM subprocess kill (``exit code -9`` SIGKILL / ``-6``
    SIGABRT) or an unreachable executor/broker (``ExecutorUnreachableError``)
    — for monitoring/alerting (surfaced via /admin/runtime/status counts).

    Best-effort: never raises. A retry/recovery path is intentionally NOT
    added here (tracked separately in the scheduling-resource plan); today the
    caller SURFACES an actionable ``infra_transient`` error to the user and
    skips the helper-LLM fallback so the failure is never masked by a
    fabricated reply. We only need visibility so the platform can alert and the
    affected user's memory cap can be tuned.
    """
    reason = classify_executor_infra_failure(error_type, error_str)
    if reason is None:
        return
    try:
        from xyz_agent_context.repository.executor_audit_repository import (
            ExecutorAuditRepository,
        )
        from xyz_agent_context.schema.executor_audit import (
            EVENT_OOM_KILLED,
            EVENT_EXECUTOR_UNREACHABLE,
        )
        from xyz_agent_context.agent_framework.llm.failure import (
            EXECUTOR_INFRA_REASON_OOM,
        )

        event_type = (
            EVENT_OOM_KILLED
            if reason == EXECUTOR_INFRA_REASON_OOM
            else EVENT_EXECUTOR_UNREACHABLE
        )
        await ExecutorAuditRepository(db_client).record(
            event_type=event_type,
            user_id=user_id,
            detail={
                "error_type": error_type,
                "error_message": error_str[:500],
                "output_already_emitted": output_already_emitted,
            },
        )
    except Exception as audit_err:  # noqa: BLE001
        logger.warning(
            f"[executor-infra-audit] failed to record {reason} event: {audit_err}"
        )


@timed("step.3_agent_loop")

async def step_3_agent_loop(
    ctx: "RunContext",
    db_client,
    response_processor
) -> AsyncGenerator[Union[ProgressMessage, PathExecutionResult, Any], None]:
    """
    Step 3: Narrative Smart Agent Loop (CASE1: AGENT_LOOP)

    Executed as Step 3, contains the following sub-steps:
    - 3.1: Initialize ContextRuntime
    - 3.2: Run ContextRuntime (build Context)
    - 3.3: Extract messages and MCP URLs
    - 3.4: Run Agent Loop (ClaudeAgentSDK)
    - 3.5: Agent's final thinking for this round

    Args:
        ctx: Run context
        db_client: Database client
        response_processor: Response processor

    Yields:
        ProgressMessage: Step 3 progress messages
        AgentTextDelta: Agent text output deltas
        PathExecutionResult: Unified execution result (returned last)
    """
    # Local variables
    context = None
    messages = []
    state = None
    agent_loop_response = []
    substeps = []  # Step 3 substep list

    # ============================================================================= Step 3: Narrative Smart Agent Loop
    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description="Build context and run Agent Loop (CASE1: implicit orchestration)",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.1: Initialize ContextRuntime -------------
    context_runtime = ContextRuntime(
        ctx.agent_id, ctx.user_id, db_client,
        # Step 0 already created the event row; passing it here is what
        # lets tools stamp attribution with the turn that called them.
        event_id=getattr(ctx.event, "id", None),
    )
    substeps.append("[3.1] ✓ ContextRuntime initialization complete")
    logger.debug("ContextRuntime initialized")

    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description="[3.1] ContextRuntime initialization complete",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.2: Run ContextRuntime -------------
    context = await context_runtime.run(
        ctx.narrative_list,
        ctx.active_instances,
        ctx.input_content,
        working_source=ctx.working_source,
        created_job_ids=ctx.created_job_ids,
        trigger_extra_data=ctx.trigger_extra_data,
    )
    substeps.append(
        f"[3.2] ✓ Context build complete: {len(context.messages)} messages, "
        f"{len(context.mcp_servers)} MCP servers"
    )
    logger.debug("ContextRuntime execution completed")

    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description=f"[3.2] Context build complete: {len(context.messages)} messages",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.3: Extract messages and MCP URLs -------------
    messages = context.messages
    ctx.mcp_servers.update(context.mcp_servers)
    # Setup-residency: tools suppressed for this agent this turn (schemas
    # removed from the model context via the CLI's disallowed_tools).
    extra_disallowed_tools = list(context.disallowed_tools or [])
    substeps.append(
        f"[3.3] ✓ Extraction complete: {len(messages)} messages, {len(ctx.mcp_servers)} MCP servers"
        + (f", {len(extra_disallowed_tools)} suppressed tools" if extra_disallowed_tools else "")
    )
    logger.debug(f"context.messages count={len(messages)}")
    logger.debug(f"context.mcp_servers={list(ctx.mcp_servers.keys())}")
    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description=f"[3.3] Extraction complete: {len(messages)} messages",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    # ------------- 3.4: Run Agent Loop -------------
    substeps.append("[3.4] ⏳ Agent Loop running...")

    yield ProgressMessage(
        step="3",
        title="Execute Agent Loop",
        description="[3.4] Agent Loop running...",
        status=ProgressStatus.RUNNING,
        substeps=substeps
    )

    state = ExecutionState()

    # Set up Agent working directory
    from xyz_agent_context.settings import settings
    working_path = settings.base_working_path
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path
    agent_working_path = str(agent_workspace_path(ctx.agent_id, ctx.user_id, base=working_path))
    if not os.path.exists(agent_working_path):
        os.makedirs(agent_working_path)

    # What this turn may reach outside its own workspace: the bus attachment
    # dir (user-wide by design — the bus stages one copy per owner and every
    # same-user recipient reads that path) plus the shared folder of the ONE
    # team this turn belongs to.
    #
    # Narrowed after review: the first version granted the whole per-user
    # `_shared` tree on every turn, which handed every team the owner has to
    # every turn, including one-to-one chats belonging to no team. That is the
    # owner-vs-membership mistake the rest of this feature deliberately avoids
    # (see `_resolve_entry` and `list_for_agent_context`), and it is not
    # read-only: the confinement layers inspect `file_path` and shell paths,
    # so the grant covers Write, Edit and rm.
    from xyz_agent_context.utils.workspace_paths import turn_accessible_roots
    _turn_extra = ctx.trigger_extra_data or {}
    extra_accessible_roots = turn_accessible_roots(
        ctx.user_id, team_id=str(_turn_extra.get("bus_team_id") or ""), base=working_path
    )

    # Extract skill-configured env vars from context for runtime injection
    skill_env_vars = {}
    if context.ctx_data and context.ctx_data.extra_data:
        skill_env_vars = context.ctx_data.extra_data.get("skill_env_vars", {})

    # `captured_error` defers the ErrorMessage yield until AFTER the
    # recovery phase, so frontend renders the recovered reply FIRST and
    # the warning badge SECOND. Yielding ErrorMessage immediately on
    # except would flip displayContent to the error string for the
    # split second before the synthetic send_message lands.
    captured_error: dict | None = None
    # Select the agent-loop framework via the registry (iron rule #9).
    # Read the user's per-agent choice from user_slots; fall back to
    # claude_code on missing row / DB hiccup. Pass the resolved name
    # into the registry — driver factories are registered under both
    # the canonical user-facing names (claude_code / codex_cli) and
    # short aliases (claude / codex), so any value we read here that
    # the system supports will resolve.
    framework_name = await _resolve_agent_framework_name(ctx.agent_id, db_client)
    # Fast-mode profiles may pin the framework (voice needs NexusPower's
    # streaming/expressive seams). None/empty override = slot resolution
    # stands untouched. The override is refused when the provider config
    # cannot serve the pinned framework — a turn that worked on the slot
    # framework must never be bricked by fast mode; the voice bridge then
    # simply sees no deltas and the legacy finalize chain delivers.
    if ctx.turn_profile is not None and ctx.turn_profile.framework_override:
        _override = ctx.turn_profile.framework_override
        if _framework_override_viable(_override):
            framework_name = _override
        else:
            logger.warning(
                f"[step_3] framework_override {_override!r} not viable for "
                f"this provider config; keeping {framework_name!r}"
            )
    logger.info(
        f"[step_3] agent_loop framework: {framework_name!r} "
        f"(agent={ctx.agent_id}, trigger_user={ctx.user_id})"
    )
    # Executor ensure/warm is INSIDE this try so a cold-start failure
    # (ExecutorUnreachableError from ensure_executor / wait_until_ready — broker
    # down or the container never boots) lands in the same except as a mid-run
    # drop, and is surfaced as an actionable ``infra_transient`` error rather
    # than escaping step_3 as a raw exception (issue ②'s bare-ClientConnectorError).
    #
    # Resume is no longer decided here (2026-07-29). The claude adapter authors
    # the CLI transcript itself every turn, so step_3 has nothing to look up,
    # validate or lease — see adapters/claude/transcript.py.
    try:
        # The materialized turn bundle — one explicit object instead of loose
        # locals, so every driver demonstrably eats the same thing.
        # driver_kwargs() reproduces the historical call shape exactly
        # (including empty→None normalization); cancellation stays separate.
        turn_input = TurnInput(
            messages=messages,
            mcp_servers=ctx.mcp_servers,
            disallowed_tools=tuple(extra_disallowed_tools),
            extra_env=skill_env_vars,
            agent_id=ctx.agent_id,
            # The turn's delivery surface, declared by the modules
            # (context 3.2). NexusPower's monologue contract routes every
            # user-visible reply through these; CLI drivers ignore them.
            expressive_tools=tuple(context.expressive_tools),
            turn_profile=ctx.turn_profile,
            extra_accessible_roots=extra_accessible_roots,
        )
        # Per-user executor routing (cloud): ask the broker to ensure this
        # user's Executor container and use its URL. Returns None when no
        # broker is configured (local/desktop, or static AGENT_EXECUTOR_URL),
        # so get_agent_loop_driver falls back. This is the cold-start point.
        from xyz_agent_context.agent_framework.loop.broker_client import (
            ensure_executor,
            wait_until_ready,
        )

        ensured = await ensure_executor(ctx.user_id)
        executor_url = ensured.url if ensured else None
        if ensured is not None and ensured.cold_started:
            # The user's executor was asleep and is being woken — emit a
            # semantic marker so the frontend can show the "waking up" overlay.
            # English text only (iron rule #1); the localized friendly copy
            # lives in the frontend, keyed on step="executor.warming".
            yield ProgressMessage(
                step="executor.warming",
                title="Waking up your agent",
                description="Your agent was idle; starting it up…",
                status=ProgressStatus.RUNNING,
            )
            # The container is started but uvicorn on :8020 takes a few seconds
            # to come up. Wait for it to be ready BEFORE driving the loop —
            # otherwise the first connection races the cold start, fails, and
            # the run drops into the fallback path instead of running the agent.
            await wait_until_ready(executor_url)
        # Identity token (blueprint P1): stamped at dispatch time because the
        # cloud token only exists once ensure() has answered. Mutates the same
        # mcp_servers dict TurnInput already references — the documented
        # pass-by-reference contract (turn_input.py: "step_3 merges into
        # mcp_servers before the call and drivers must see the merged dict").
        identity_token = _dispatch_identity_token(ensured, ctx.user_id)
        if identity_token and ctx.mcp_servers:
            from xyz_agent_context.module import stamp_identity_token

            stamp_identity_token(ctx.mcp_servers, identity_token)
        # Platform-origin binding: stamp the same token onto this turn's provider
        # configs (in THIS task context, before the driver snapshots them) so it
        # rides provider_configs to the executor and is emitted as the
        # X-NarraNexus-Identity-Token header on our-gateway LLM calls. Where the
        # deploy-side check is live (litellm/prefill_compat._enforce_identity —
        # deploy `staging` PR #20; not `dev`/`main` yet) the gateway verifies it,
        # so an exfiltrated wallet key is useless off-platform THERE; until it
        # lands the header is a harmless no-op.
        if identity_token:
            from xyz_agent_context.agent_framework.api_config import (
                bind_platform_identity,
            )

            bind_platform_identity(identity_token)
        driver = get_agent_loop_driver(
            framework=framework_name,
            executor_url=executor_url,
            working_path=agent_working_path,
        )
        # Clear the "waking up" overlay the instant the (now-awake) executor
        # emits its first event — the COMPLETED that pairs the RUNNING above.
        _warming_active = ensured is not None and ensured.cold_started
        async for response in driver.agent_loop(
            cancellation=ctx.cancellation,
            **turn_input.driver_kwargs(),
        ):
            if _warming_active:
                _warming_active = False
                yield ProgressMessage(
                    step="executor.warming",
                    title="Agent ready",
                    description="Your agent is awake.",
                    status=ProgressStatus.COMPLETED,
                )
            # ResponseProcessor.process is a generator yielding 0..N
            # ProcessedResponse per raw event (Phase B 2026-05-13 —
            # thinking deltas get coalesced via _ThinkingBatcher, and a
            # non-thinking event may emit a buffered-thinking flush
            # FIRST plus the actual event SECOND).
            for result in response_processor.process(response, state):
                state = response_processor.apply_state_update(state, result)
                if result.message is not None:
                    agent_loop_response.append(result.message)
                    yield result.message
        # End-of-stream — flush any residual thinking buffer so the last
        # partial thinking segment is not silently dropped.
        for result in response_processor.flush_pending(state):
            state = response_processor.apply_state_update(state, result)
            if result.message is not None:
                agent_loop_response.append(result.message)
                yield result.message
    except Exception as e:
        # Before deferring the error, drain any residual thinking buffer
        # so the user does not lose their last partial thinking on an
        # exception path. Best-effort: errors here are logged but never
        # re-raise.
        try:
            for result in response_processor.flush_pending(state):
                state = response_processor.apply_state_update(state, result)
                if result.message is not None:
                    agent_loop_response.append(result.message)
                    yield result.message
        except Exception as flush_err:  # noqa: BLE001
            logger.warning(f"Failed to flush thinking buffer on error path: {flush_err}")

        # Capture the fatal for later: the recovery phase below decides
        # whether we surface it as severity=recovered (helper_llm wrote
        # a usable reply), severity=recovered_after_reply (agent already
        # spoke before crash), or severity=fatal (no recovery possible).
        error_str = str(e)
        error_type = type(e).__name__
        logger.exception(f"[AGENT-LOOP-FATAL] {error_type}: {error_str}")
        captured_error = {"error_type": error_type, "error_message": error_str}

        # Executor-infra failure (OOM SIGKILL/SIGABRT, or unreachable
        # executor/broker): record for monitoring + alerting. Retry is deferred
        # (scheduling-resource plan). The recovery phase below surfaces it as a
        # fatal ``infra_transient`` error and SKIPS the fallback, so it is never
        # masked by a fabricated reply.
        await _record_executor_infra_event(
            db_client, ctx.user_id, error_type, error_str, bool(agent_loop_response)
        )
    # Finalize state BEFORE inspecting it — accessing `state.final_output`
    # on an unfinalized state is undefined per ExecutionState's contract.
    state = state.finalize()

    # ------------- 3.4.X: Post-loop recovery phase -------------
    # Three modes cover the recovery slot:
    #   - no_reply: clean loop, agent forgot to call send_message →
    #     helper_llm writes the missing reply using the agent's
    #     reasoning + context.
    #   - after_error: loop crashed mid-stream with no organic reply →
    #     helper_llm writes a recovery reply using full context
    #     (system prompts + completed tool results + error info).
    #   - partial_reply_then_error: loop crashed AFTER an organic reply →
    #     no helper_llm (we already spoke), but a warning badge
    #     surfaces the truncated execution.
    # Out-of-scope triggers (non-chat) and cancellation are skipped.
    #
    # A turn that failed a way the USER must fix — dead credentials (auth) or
    # a DETERMINISTIC self-serviceable config error (context window too small,
    # no credits, bad model id) — is NOT recoverable by a helper reply: the
    # agent (tools / MCP / memory) never ran. Fabricating a reply masks the
    # fixable cause and misleads the user (the "black box" P1 + incident
    # 2026-06-11: a used codex token silently degraded to gpt-5 every turn).
    # Two sub-cases both skip the fallback and fall through to the
    # PathExecutionResult below (Step 4 persists the failed turn like any
    # other):
    #   (a) inline path — response_processor already surfaced the fatal,
    #       actionable ErrorMessage (``auth_expired`` / ``config_actionable``);
    #       it's already in agent_loop_response. Just skip the fallback.
    #   (b) raw-exception path — the loop raised a Python exception, so
    #       ``captured_error`` is set but NO ErrorMessage exists yet. If it's
    #       self-serviceable (class name preserved, e.g.
    #       ``ContextWindowExceededError``), emit the fatal actionable
    #       ErrorMessage here (mirroring response_processor) and skip the
    #       fallback — otherwise the error would be completely invisible.
    skip_kind, skip_reason_detail, skip_target_type = _fallback_skip_decision(
        agent_loop_response, captured_error
    )

    # Runtime model-health feedback: a classified model_not_found means the
    # bound model was definitively rejected by its endpoint. Report the acting
    # slot's (source, model, protocol) as a probe suspect so the next model
    # sync revalidates it ahead of the TTL queue and dead entries leave the
    # dropdowns (providers/model_health). Best-effort by contract.
    _mnf = SELF_SERVICEABLE_REASON_MODEL_NOT_FOUND
    model_rejected = (
        skip_kind == "raw_exception"
        and skip_target_type == SELF_SERVICEABLE_ERROR_TYPE
        and skip_reason_detail == _mnf
    ) or (
        skip_kind == "inline"
        and any(
            isinstance(m, ErrorMessage)
            and getattr(m, "error_type", "") == SELF_SERVICEABLE_ERROR_TYPE
            and getattr(m, "action_reason", None) == _mnf
            for m in agent_loop_response
        )
    )
    if model_rejected:
        from xyz_agent_context.agent_framework.providers.model_health import (
            report_agent_slot_suspect,
        )

        await report_agent_slot_suspect(
            db_client, user_id=ctx.user_id, agent_id=ctx.agent_id, reason=_mnf
        )

    if skip_kind == "inline":
        logger.warning(
            "[FALLBACK] skipped: turn failed a user-fixable way (auth / "
            "config_actionable) — surfacing the actionable error instead of "
            "fabricating a reply"
        )
    elif skip_kind == "raw_exception":
        # Compose the right actionable copy for the failure class: platform-side
        # executor-infra ("retry / split the task") vs user-fixable config
        # ("change a setting"). Both skip the fallback so neither is masked.
        is_infra = skip_target_type == EXECUTOR_INFRA_ERROR_TYPE
        failure_class = "executor-infra" if is_infra else "self-serviceable"
        logger.warning(
            f"[FALLBACK] skipped: {failure_class} error ({skip_reason_detail}) "
            f"— surfacing actionable error instead of masking it with a "
            f"fabricated reply"
        )
        raw_detail = captured_error.get("error_message", "")
        message = (
            executor_infra_user_message(skip_reason_detail, raw_detail)
            if is_infra
            else self_serviceable_user_message(skip_reason_detail, raw_detail)
        )
        # If the agent ALREADY sent a real reply before this failure (an executor
        # OOM/drop can hit AFTER send_message_to_user_directly), surface a warning
        # badge (recovered_after_reply), not a hard "retry" fatal — the user got
        # their answer, and telling them to resend would re-run a done turn. Only
        # a no-reply failure is fatal. (Self-serviceable errors fire before the
        # agent runs, so this is virtually always "fatal" for them.)
        severity = (
            "recovered_after_reply"
            if _has_organic_reply(agent_loop_response, ctx.working_source or "")
            else "fatal"
        )
        err = ErrorMessage(
            error_message=message,
            error_type=skip_target_type,
            severity=severity,
            action_reason=skip_reason_detail,
        )
        agent_loop_response.append(err)
        yield err
    else:
        # The channel trigger forwards the room type and this channel's
        # delivery kwargs in the generic turn envelope (see
        # ChannelContextBuilderBase.turn_envelope). Absent envelope =
        # not an IM turn = no DM fallback, which is the safe default for
        # chat / job / bus.
        # NOTE: `context` (the ContextRuntime OUTPUT), not `ctx`. ContextData
        # is built fresh inside this step and hangs off the output — `ctx`
        # has no `ctx_data` attribute, so passing it here silently produced
        # an empty envelope and made the whole IM DM fallback dead code
        # (caught in live Telegram testing 2026-08-06: the prompt carried
        # the DM protocol while the decision logged group_room).
        channel_envelope = _channel_turn_envelope(context)
        fallback_mode, skip_reason = _should_run_helper_llm_fallback(
            working_source=ctx.working_source or "",
            agent_loop_response=agent_loop_response,
            cancellation=getattr(ctx, "cancellation", None),
            is_direct_message=(
                channel_envelope.get("channel_room_type") == ROOM_TYPE_DIRECT
            ),
        )
        # One unconditional line per turn recording what the recovery slot
        # decided AND the room type it decided on. The prompt and this
        # decision read the SAME room_type, so "prompt said Direct Message,
        # decision said group_room" is the exact signature of a broken
        # envelope — and it was invisible before, because the
        # already_replied case logs nothing and the wrong branch looked
        # like a legitimate skip. That blind spot cost one dead-code
        # release of this fallback (caught only by reading the DB during
        # live Telegram testing, 2026-08-06).
        logger.info(
            f"[FALLBACK] decision: mode={fallback_mode!r} skip_reason={skip_reason!r} "
            f"working_source={ctx.working_source!r} "
            f"room_type={channel_envelope.get('channel_room_type', '')!r} "
            f"has_reply_kwargs={bool(channel_envelope.get('channel_reply_kwargs'))}"
        )
        if fallback_mode is None and skip_reason != "already_replied_via_tool":
            logger.info(
                f"[FALLBACK] skipped: skip_reason={skip_reason!r} "
                f"(captured_error={captured_error!r})"
            )
        if fallback_mode is not None:
            logger.warning(
                f"[FALLBACK] mode={fallback_mode} "
                f"(reasoning_chars={len(state.final_output)}, "
                f"captured_error={bool(captured_error)})"
            )

        async for msg in _stream_fallback_recovery(
            fallback_mode=fallback_mode,
            captured_error=captured_error,
            context_messages=messages,
            agent_loop_response=agent_loop_response,
            final_output=state.final_output,
            user_input=ctx.input_content,
            cancellation=getattr(ctx, "cancellation", None),
            db=db_client,
            agent_id=ctx.agent_id,
            working_source=ctx.working_source or "",
            channel_tag={
                **(channel_envelope.get("channel_tag") or {}),
                "agent_id": ctx.agent_id,
            },
            reply_kwargs=channel_envelope.get("channel_reply_kwargs") or {},
        ):
            agent_loop_response.append(msg)
            yield msg

    # Update 3.4 sub-step to completed status
    substeps[-1] = (
        f"[3.4] ✓ Agent Loop complete: {state.response_count} responses, "
        f"{len(state.final_output)} chars output"
    )
    logger.info(f"Agent Loop completed: {state.response_count} responses received")
    logger.debug(f"agent_loop.final_output_chars={len(state.final_output)}")

    # ------------- 3.5: Agent's final thinking for this round -------------
    final_output_preview = (
        state.final_output[:200] + "..."
        if len(state.final_output) > 200
        else state.final_output
    )
    substeps.append("[3.5] Agent's final thinking for this round")

    yield ProgressMessage(
        step="3.5",
        title="Agent's Final Thinking for This Round",
        description=final_output_preview,
        status=ProgressStatus.COMPLETED,
        details={
            "final_output": state.final_output,
            "output_length": len(state.final_output)
        }
    )

    # Step 3 complete
    yield ProgressMessage(
        step="3",
        title="Agent Loop Complete",
        description=f"✓ Complete: {state.response_count} responses, {len(state.final_output)} chars output",
        status=ProgressStatus.COMPLETED,
        details={
            "response_count": state.response_count,
            "output_length": len(state.final_output),
            "mcp_servers": list(ctx.mcp_servers.keys())
        },
        substeps=substeps
    )

    # CLI session handle companions — only filled when the run reported a
    # resumable session id (Claude Code's ResultMessage only, in v1). The
    # canonical framework name and the config fingerprint were computed up
    # front by the resume-decision block (before 3.4), in the scope where the
    # ambient per-task claude_config ContextVar is guaranteed live — step_4
    # never recomputes. Fail-open there means the fingerprint may be None,
    # in which case step_4 skips persistence — resume capture must never
    # hurt a turn.

    # Return unified execution result
    yield PathExecutionResult(
        final_output=state.final_output,
        execution_steps=state.get_all_steps_as_list(),
        response_count=state.response_count,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        model=state.model,
        total_cost_usd=state.total_cost_usd,
        cache_read_tokens=state.cache_read_tokens,
        cache_creation_tokens=state.cache_creation_tokens,
        num_turns=state.num_turns,
        cli_session_id=state.cli_session_id,
        # Propagated even without a new session id: step_4 must delete the
        # stale handle regardless of whether the cold retry reported one.
        agent_loop_response=agent_loop_response,
        ctx_data=context.ctx_data,
    )
