"""
@file_name: _basic_info_mcp_tools.py
@author: Bin Liang
@date: 2026-05-20
@description: MCP server + narrative-awareness tools for BasicInfoModule (Fix #2 P3).

The agent's conversation history (built by ChatModule.hook_data_gathering) is a
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

from loguru import logger
from mcp.server.fastmcp import FastMCP


# Tool names the runtime hook scans agent_loop_response for (keep in lockstep
# with step_4_persist_results._detect_narrative_routing_signal).
SWITCH_NARRATIVE_TOOL = "switch_narrative"
CREATE_NARRATIVE_TOOL = "create_narrative"


def create_basic_info_mcp_server(port: int) -> FastMCP:
    """Create the BasicInfoModule MCP server with the narrative + feedback tools."""
    mcp = FastMCP("basic_info_module")
    mcp.settings.port = port
    _register_narrative_tools(mcp)
    _register_feedback_tool(mcp)
    logger.info(
        f"BasicInfo MCP: tools registered on port {port} "
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
    the agent must not retry or dwell on it."""

    @mcp.tool(
        name="submit_feedback",
        description=(
            "Report a product problem to the NarraNexus team. Call when (a) the "
            "user expresses dissatisfaction, frustration or disappointment with "
            "how you/the product behaved, or (b) you have failed the SAME user "
            "instruction 2+ times in a row. `category` is one of: "
            "user_dissatisfaction | repeated_failure | error | feature_gap | other. "
            "`severity` is low | medium | high. `summary` must be ONE sentence "
            "describing the PROBLEM in your own words — never quote the user's "
            "messages, never include names, keys or file contents. This tool "
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
    ) -> dict:
        from xyz_agent_context.integrations.feedback_client import send_feedback

        delivered = await send_feedback(
            category=category,
            summary=summary,
            severity=severity,
            source="agent",
            agent_id=agent_id,
            user_id=user_id,
        )
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
        from xyz_agent_context.module.data_access import get_agent_data_store

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
        from xyz_agent_context.module.data_access import get_agent_data_store

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
        from xyz_agent_context.module.data_access import get_agent_data_store

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
