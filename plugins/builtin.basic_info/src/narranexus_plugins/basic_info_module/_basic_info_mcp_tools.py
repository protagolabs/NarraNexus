"""
@file_name: _basic_info_mcp_tools.py
@author: Bin Liang
@date: 2026-05-20
@description: MCP server + narrative-awareness tools for BasicInfoModule (Fix #2 P3).

The agent's conversation history (built by ChatModule.gather) is a
single time-sorted timeline merging the current thread (full) with the latest
~30 lines across the user's OTHER threads, each line tagged
`[<time> · <topic> · nar=<narrative_id> · evt=<event_id>]`, plus a separate
"recent background activity" list (each with an evt= id). The system PICKS a
default narrative for the turn, but that pick isn't always right. These four
tools give the agent visibility + agency over it:

  - view_narrative(narrative_id): full info on a thread incl. ALL its chat
    history (the timeline only shows the latest trimmed slice).
  - view_event(event_id): one past turn's full agent-loop / reasoning detail
    (the timeline only carries the message that was sent to the user).
  - switch_narrative(narrative_id): declare that THIS turn belongs to that
    existing thread — the runtime re-attributes this turn's event + memory.
  - create_narrative(title, description): declare THIS turn starts a NEW thread.

switch/create are SIGNALS. The tool validates/creates and returns the target id;
the agent_runtime hook (step_4_persist_results) detects the call and does the
actual re-attribution. The tool process and the runtime are different processes,
so detection-from-the-tool-call is how they communicate (no shared state).
"""
from __future__ import annotations

import time
from collections import OrderedDict

from loguru import logger
from mcp.server.fastmcp import FastMCP


# Tool names the runtime hook scans agent_loop_response for (keep in lockstep
# with step_4_persist_results._detect_narrative_routing_signal).
SWITCH_NARRATIVE_TOOL = "switch_narrative"
CREATE_NARRATIVE_TOOL = "create_narrative"


# ── submit_feedback dedup (2026-09-09) ───────────────────────────────────────
# Feedback triggers (a)/(b) are rate-limited by human interaction: the user has
# to complain, or the same instruction has to fail twice. Trigger (c) is
# machine-generated — a platform-side outage reproduces on EVERY tool call, and
# it is platform-WIDE, so every affected agent hits it every turn. "File it
# once" therefore cannot be left to the model's memory: a long agent_loop
# (铁律 #14) gets its context compacted, and this tool always answers ok=True,
# which reinforces re-filing. The gate lives here so the agent's own
# confirmation enforces the rule.
#
# Single-process assumption (铁律 #20): the basic_info MCP server is one process
# shared by the deployment — this does NOT dedup across processes or restarts,
# and it is deliberately not a substitute for intake-side idempotency. Bounded
# by TTL + entry count so a caller-supplied key can never grow it without limit.
FEEDBACK_DEDUP_TTL_SECONDS = 6 * 3600
FEEDBACK_DEDUP_MAX_ENTRIES = 4096
FEEDBACK_DEDUP_KEY_MAXLEN = 120

_feedback_dedup: "OrderedDict[tuple[str, str], float]" = OrderedDict()


def _dedup_slot(agent_id: str, dedup_key: str) -> tuple[str, str]:
    return (agent_id, dedup_key[:FEEDBACK_DEDUP_KEY_MAXLEN])


def _dedup_reserve(slot: tuple[str, str]) -> bool:
    """Claim `slot`. False = already claimed inside the TTL, so skip the send.

    Reserving BEFORE the send (rather than recording after it) is what closes
    the window two concurrent calls would otherwise both pass through. Entries
    are inserted once and never refreshed, so insertion order is time order and
    expiry can be swept from the front.
    """
    now = time.monotonic()
    while _feedback_dedup:
        oldest, stamp = next(iter(_feedback_dedup.items()))
        if now - stamp < FEEDBACK_DEDUP_TTL_SECONDS:
            break
        _feedback_dedup.pop(oldest, None)

    if slot in _feedback_dedup:
        return False
    _feedback_dedup[slot] = now
    while len(_feedback_dedup) > FEEDBACK_DEDUP_MAX_ENTRIES:
        _feedback_dedup.popitem(last=False)
    return True


def _dedup_release(slot: tuple[str, str]) -> None:
    """Undo a reservation whose send did not reach the intake.

    Without this the cache would hold a FAILURE sentinel: an intake outage
    would silently consume the one report this agent+code is allowed to make,
    and the team would never learn about the platform error. Releasing means a
    failed send behaves exactly like today's un-deduped code (the next call
    tries again); only a DELIVERED report suppresses repeats.
    """
    _feedback_dedup.pop(slot, None)


def create_basic_info_mcp_server() -> FastMCP:
    """Create the BasicInfoModule MCP server with the narrative + feedback tools."""
    mcp = FastMCP("basic_info_module")
    _register_narrative_tools(mcp)
    _register_feedback_tool(mcp)
    logger.info(
        "BasicInfo MCP: tools registered "
        f"(view_narrative, view_event, {SWITCH_NARRATIVE_TOOL}, {CREATE_NARRATIVE_TOOL}, "
        f"submit_feedback)"
    )
    return mcp


def _register_feedback_tool(mcp: FastMCP) -> None:
    """submit_feedback — the agent's channel for telling the NarraNexus team
    something went wrong.

    Privacy: feedback_client enforces what it CAN (identifiers hashed, summary
    truncated); the CONTENT of the summary — no user quotes, no keys/PII — is
    prompt-governed (prompts.py Product Feedback Duty) and not verifiable in
    code. The tool always answers ok=True — delivery is fire-and-forget and
    the agent must not retry or dwell on it, including when a report is
    suppressed as a duplicate (see the dedup block above)."""

    @mcp.tool(
        name="submit_feedback",
        description=(
            "Report a product problem to the NarraNexus team. Call when (a) the "
            "user expresses dissatisfaction, frustration or disappointment with "
            "how you/the product behaved, (b) you have failed the SAME user "
            "instruction 2+ times in a row, or (c) a credential / endpoint / "
            "quota the PLATFORM injects for you is rejected by a platform tool "
            "(`agent-token-invalid`, an unexpected 401/403 from a working "
            "binding) — file it with category `error` naming the tool and the "
            "error code, even if a retry later works, and pass "
            "`dedup_key=\"<tool>:<code>\"`: this tool then files it ONCE per "
            "agent per tool+code and silently drops repeats, so you never have "
            "to remember what you already filed. Not (c): `no_credential` when "
            "nothing is bound, by-design policy refusals like "
            "`official-agent-required`, or a secret the user just "
            "typed being rejected. If a failure satisfies both (b) and (c), "
            "file it as (c) only. `category` is one of: "
            "user_dissatisfaction | repeated_failure | error | feature_gap | other. "
            "`severity` is low | medium | high. `summary` must be ONE sentence "
            "describing the PROBLEM in your own words — never quote the user's "
            "messages, never include personal names, secrets/keys or file "
            "contents (the TOOL name and the error code are required for (c) — "
            "they are not secrets). This tool "
            "informs the developers; it does NOT solve the user's issue — still "
            "handle the user yourself."
        ),
    )
    async def submit_feedback(
        agent_id: str,
        user_id: str,
        category: str,
        summary: str,
        severity: str = "medium",
        dedup_key: str = "",
    ) -> dict:
        from narranexus.platform.integrations.feedback_client import send_feedback

        # Optional on purpose: triggers (a)/(b) call without it and must keep
        # working. An empty key means "no dedup", never "dedup everything".
        slot = _dedup_slot(agent_id, dedup_key) if dedup_key else None
        if slot is not None and not _dedup_reserve(slot):
            logger.debug(
                f"[feedback] duplicate suppressed category={category} "
                f"dedup_key={slot[1]}"
            )
            # ok=True, not an error: the tool contract forbids the agent from
            # retrying or apologising about telemetry.
            return {
                "ok": True,
                "message": "Already filed this one. Keep working on the user's problem.",
            }

        delivered = await send_feedback(
            category=category,
            summary=summary,
            severity=severity,
            source="agent",
            agent_id=agent_id,
            user_id=user_id,
        )
        if slot is not None and not delivered:
            _dedup_release(slot)
        logger.info(
            f"[feedback] agent report category={category} severity={severity} "
            f"delivered={delivered}"
        )
        # Always ok — the agent shouldn't retry or apologise about telemetry.
        return {"ok": True, "message": "Feedback recorded. Continue helping the user."}


def _register_narrative_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="view_narrative",
        description=(
            "Look up one conversation thread (narrative) IN FULL by its id — "
            "including its entire chat history. Your conversation history is a "
            "merged, time-trimmed timeline (latest ~30 lines across threads), so "
            "an older thread may be partly cut off. Take a narrative_id from any "
            "message tag `[.. nar=<id> ..]` and pass it here to read that whole "
            "thread before deciding how to respond. Returns {name, description, "
            "summary, keywords, message_count, messages:[{time, role, content, "
            "event_id}]}."
        ),
    )
    async def view_narrative(agent_id: str, narrative_id: str) -> dict:
        # Routes through the AgentDataStore seam (DirectStore locally / HttpStore
        # in cloud). The old raw MySQL is gone — the read is now dialect-safe and
        # scoped to this agent (the tool used to return ANY agent's narrative).
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().view_narrative(agent_id, narrative_id)

    @mcp.tool(
        name="view_event",
        description=(
            "Get one past turn's FULL detail by its event id (the `evt=<id>` in "
            "a timeline message tag or a recent-activity line). The timeline only "
            "shows the message you SENT; this returns that turn's full agent-loop "
            "trace and reasoning. Returns {final_output, trigger, time, event_log}."
        ),
    )
    async def view_event(agent_id: str, event_id: str) -> dict:
        # Routes through the seam (see view_narrative). The raw `trigger`
        # backtick MySQL is gone; the read is dialect-safe and agent-scoped.
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().view_event(agent_id, event_id)

    @mcp.tool(
        name=SWITCH_NARRATIVE_TOOL,
        description=(
            "Declare that THIS turn belongs to a DIFFERENT existing thread than "
            "the one the system defaulted to. Use it when the user's message "
            "(especially a short reply like '好'/'yes') is actually continuing "
            "another thread shown in your timeline — pass that thread's "
            "narrative_id (from its `[.. nar=<id> ..]` tag). The system will "
            "re-file this turn into that thread so future context stays correct. "
            "Call this BEFORE you reply. If none fits and it's a new topic, use "
            "create_narrative instead."
        ),
    )
    async def switch_narrative(agent_id: str, narrative_id: str) -> dict:
        # Routes through the seam (see view_narrative). Now agent-scoped: the old
        # raw SQL validated existence but not ownership. The result key is
        # `success` (aligned with the other seam tools), not the old `ok`.
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().switch_narrative(agent_id, narrative_id)

    @mcp.tool(
        name=CREATE_NARRATIVE_TOOL,
        description=(
            "Declare that THIS turn starts a brand-NEW conversation thread "
            "(topic) — use it when the user's message doesn't belong to any "
            "thread in your timeline. Provide a short title and one-line "
            "description. The system creates the thread and files this turn into "
            "it. Call this BEFORE you reply. Returns {narrative_id}."
        ),
    )
    async def create_narrative(agent_id: str, user_id: str, title: str, description: str = "") -> dict:
        # SIGNAL only: the runtime hook (step_4) reads {title, description} from
        # this call, CREATES the narrative, and files this turn into it. We do
        # not create here so the tool process and runtime don't double-create.
        if not (title or "").strip():
            return {"ok": False, "error": "title is required"}
        logger.info(f"[NarrativeTool] create_narrative signal: title={title!r} (agent={agent_id})")
        return {
            "ok": True,
            "title": title,
            "message": "Noted — a new narrative with this title will be created and this turn filed into it.",
        }
