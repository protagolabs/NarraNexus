"""
@file_name: message_bus_module.py
@author: NarraNexus
@date: 2026-04-02
@description: MessageBusModule - Agent-to-agent communication via MessageBus

Protocol-agnostic message bus for agent-to-agent communication. Provides MCP
tools for sending/receiving messages, managing channels, and discovering agents.

Instance level: Agent-level (one per Agent, is_public=True).

Behavior design:
- Reply Discipline: prevent infinite trigger loops between agents
- Selective mark_read: a DM the agent ignores stays unread and resurfaces next
  turn. NOT true of a team room, which delivers by rendering its scrollback and
  advances the read cursor once a turn has rendered the window, answered or not
  (a backlog reaching below that window holds the cursor — see `_ack_room_seen`)
- Context caps: unread/channels/known_agents all bounded to prevent pollution
- Source recognition: entries in the unread list are tagged
  [MessageBus · sender · channel] (see `_bus_tag`). This is the ONLY place the
  tag appears — a branch that prefixed the turn's INPUT with it was deleted on
  2026-08-17 after it turned out never to have executed; do not describe an
  input prefix here again without checking that one exists
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.module.base import (
    XYZBaseModule,
    mcp_host,
    working_source_matches,
)
from xyz_agent_context.schema import (
    BUS_TEAM_ROOM_EXTRA_KEY,
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
    is_agent_description_unset,
)
from xyz_agent_context.message_bus.system_messages import (
    PLATFORM_MSG_TYPES,
    SYSTEM_SENDER_LABEL,
)
from xyz_agent_context.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
)
from xyz_agent_context.settings import settings


# MCP server port for MessageBus tools
MESSAGE_BUS_MCP_PORT = 7820

# Context-injection caps to prevent pollution
MAX_UNREAD_IN_CONTEXT = 20
MAX_CHANNELS_IN_CONTEXT = 20
MAX_KNOWN_AGENTS_IN_CONTEXT = 50


def _render_sender(from_agent: Any, msg_type: Any = None) -> str:
    """How a bus sender is named inside a `[MessageBus · … ]` tag.

    THREE kinds of sender reach this list, and only one of them is an agent.
    `schema/team_schema` defines both synthetic prefixes side by side, each
    marked "a non-agent sender":

    * ``usr_<user_id>`` — a PERSON. A team room carries its owner's own
      messages, and the raw id says nothing at all, least of all that the
      reader is looking at the person it works for.
    * ``team_<team_id>`` — the ROOM itself, i.e. the platform. Printing it
      verbatim invents a teammate the agent may then try to @mention back,
      which is exactly why `message_bus_trigger._who` refuses to.
    * anything else — a real agent.

    ``msg_type`` outranks the sender, and has to: a bulletin notice posted
    from the UI carries `msg_type=system_bulletin` but is stamped with the
    OWNER's ``usr_`` id (`team_bulletin.py`, no actor), so on the sender alone
    it reads as the owner speaking — and the Source-Recognition rules now say
    in as many words that a `User` sender is a person. Checking the type first
    is what stops the platform being quoted as the human.

    Named to match `message_bus_trigger._sender`, which renders the same rows
    in the room's own prompt — but the agreement covers only the two synthetic
    kinds: `usr_*` is "User" on both sides and a platform row is
    ``SYSTEM_SENDER_LABEL`` on both. An ORDINARY agent deliberately differs:
    the room prompt resolves it through `member_map` to a display name, while
    this list keeps the raw ``agent_id`` because that is what `bus_send_to_agent`
    takes. Worth knowing, since a team turn shows both — "Alice: …" in the
    scrollback and `agent_x7f3…` in the list, for one peer.
    """
    if (str(msg_type or "")) in PLATFORM_MSG_TYPES:
        return SYSTEM_SENDER_LABEL
    sender = str(from_agent or "unknown")
    if sender.startswith(TEAM_ROOM_OWNER_PREFIX):
        return SYSTEM_SENDER_LABEL
    if sender.startswith(USER_SENDER_PREFIX):
        return "User"
    return sender


def _bus_tag(from_agent: Any, channel_id: Any, msg_type: Any = None) -> str:
    """The `[from · sender · where]` marker, built in exactly one place.

    Renamed off "MessageBus" on 2026-08-17. The word named a subsystem the agent
    is no longer supposed to know exists — it thinks in "a private message" and
    "a team room", and a tag announcing the transport underneath is a third
    concept doing no work. The acceptance criterion for the redesign is that
    `MessageBus` appears in NO agent-visible text.

    The format had three copies — the worked example in the instructions and
    two render sites — and the example disagreed with both, showing a display
    name AND an id where the code emits a single sender field. That was
    survivable while the tag was decoration. It stopped being survivable when
    the rules started telling the agent to READ the sender out of it: an agent
    following a four-field example against a three-field tag looks for a name
    that is never there, and the field it finds in position two is the channel.

    So the example is generated by this same function, and the two can no
    longer drift apart. ``msg_type`` is optional precisely so the example stays
    constructible from two literals.
    """
    return f"[from {_render_sender(from_agent, msg_type)} · {channel_id}]"


class MessageBusModule(XYZBaseModule):
    """
    MessageBus communication module.

    Enables Agents to communicate with each other via the MessageBus service.
    Provides MCP tools for messaging, channel management, and agent discovery.

    Instance level: Agent-level (one per Agent, is_public=True).
    """

    # =========================================================================
    # Configuration
    # =========================================================================

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name="MessageBusModule",
            priority=5,
            enabled=True,
            description=(
                "Agent-to-agent communication via message bus. "
                "Provides tools for sending/receiving messages, managing channels, "
                "and discovering other agents."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="message_bus_module",
            server_url=f"http://{mcp_host()}:{MESSAGE_BUS_MCP_PORT}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        try:
            # Use the official mcp SDK's FastMCP (same as every other module)
            # so FASTMCP_HOST, TransportSecuritySettings, and the shared
            # module_runner._run_mcp_in_thread configuration all apply
            # uniformly. The standalone `fastmcp` v2 package has a different
            # API and does not honour those settings, which caused this MCP
            # to silently fail cross-container reachability.
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("message_bus_module")
            mcp.settings.port = MESSAGE_BUS_MCP_PORT

            from ._message_bus_mcp_tools import register_message_bus_mcp_tools
            register_message_bus_mcp_tools(mcp, get_message_bus_fn=_get_default_bus_async)
            # The team work board rides the same MCP server: its items are
            # scoped to a team ROOM, which is a bus channel, so an agent that
            # can talk in the room is exactly the agent that can maintain its
            # board. Separate file, separate state machine — see
            # _work_board_mcp_tools for the platform/model write boundary.
            from ._work_board_mcp_tools import register_work_board_mcp_tools
            register_work_board_mcp_tools(mcp)

            logger.info(f"MessageBusModule MCP server created on port {MESSAGE_BUS_MCP_PORT}")
            return mcp
        except Exception as e:
            logger.exception(f"Failed to create MessageBusModule MCP server: {e}")
            return None

    # =========================================================================
    # Reply surface (origin-aware declaration)
    # =========================================================================

    #: Last ctx_data seen by `get_expressive_tools` — the disallow hook is
    #: not handed one and must answer about the SAME turn.
    _last_ctx: Any = None

    def owns_working_source(self, working_source: Any) -> bool:
        """This module is the origin of MESSAGE_BUS turns — the collection
        sorts the origin module's declaration first, so the bus delivery
        tool becomes the turn's default reply tool."""
        return working_source_matches(working_source, WorkingSource.MESSAGE_BUS.value)

    #: The turn's ctx, remembered because `get_disallowed_tools` is called
    #: separately and is handed none. Same seam ChatModule uses, for the same
    #: reason: if the two hooks disagreed about which turn this is, the desk would
    #: end up with both send verbs or neither.
    _last_ctx: Any = None

    def _is_team_turn(self, ctx_data: Any = None) -> bool:
        """Is this turn a team room?

        Reads the marker MessageBusTrigger stamps into `trigger_extra_data`
        (`bus_team_room`), which reaches `ctx_data.extra_data`.
        """
        if ctx_data is not None:
            self._last_ctx = ctx_data
        ctx = ctx_data if ctx_data is not None else self._last_ctx
        extra = getattr(ctx, "extra_data", None) or {}
        return bool(extra.get(BUS_TEAM_ROOM_EXTRA_KEY))

    async def get_expressive_tools(self, ctx_data: Any = None) -> list[str]:
        """The peer/room tools this turn can deliver through.

        Declared only on a bus turn — advertising them on an owner-chat turn
        invites replying to the owner over the bus (2026-08-04). The team-room
        carve-out that used to sit here is GONE: a team reply is now a tool call
        like every other surface, so there is no longer a turn whose delivery
        happens without one.
        """
        self._last_ctx = ctx_data
        if not self.owns_working_source(getattr(ctx_data, "working_source", None)):
            return []
        config = await self.get_mcp_config()
        extra = getattr(ctx_data, "extra_data", None) or {}
        name = "message_team" if extra.get(BUS_TEAM_ROOM_EXTRA_KEY) else "message_agent"
        return [f"mcp__{config.server_name}__{name}"]

    async def get_disallowed_tools(self) -> list[str]:
        """Take the send verb that does NOT apply this turn off the desk.

        The declaration above only decides what the reply REMINDER names. The
        schemas reach the model separately, so without this the agent sees both
        `message_agent` and `message_team` and has to choose — and the wrong
        branch posts into the wrong conversation, which is the one mistake this
        redesign is built to make impossible.

        Prose cannot do this job. On prod the two tools whose own docstrings said
        "Do NOT call" were the second and fourth most-called in this family: 615
        calls. What decides whether a tool is used is whether its schema is in the
        context.

        Takes no ctx_data (base signature), so the turn is read from the same
        instance state the declaration used.
        """
        config = await self.get_mcp_config()
        drop = "message_agent" if self._is_team_turn() else "message_team"
        return [f"mcp__{config.server_name}__{drop}"]

    def _static_instruction_parts(self) -> list:
        """The usage-rules half of the instruction — constant for a given agent,
        so it can live in the byte-stable system prompt (R4 relocation).

        BYTE-STABLE AND SURFACE-BLIND: this block is emitted on every
        bus-enabled turn — owner chat, job, each IM channel, peer DM, team room —
        one character identical. Two rules follow and neither is optional:

        1. **Every sentence here must be true on every one of those surfaces.**
           A claim that holds in only one kind of room creates a contradiction
           inside one context window, because that room's own turn prompt is a
           few dozen lines away saying the opposite. Six review rounds on PR
           #311 went to exactly this.
        2. **Never branch on room type.** Branching destroys the byte-stability
           this block exists for. Say what holds everywhere and leave the
           room-specific fact to the room's own prompt — the only place that
           knows it.

        Corollaries, each one paid for: do not infer where a turn came from
        from the ABSENCE of a marker (wrong twice); do not describe a mechanism
        without checking it exists (a whole section once pointed at a branch
        that had never run); when you change wording here, sweep the other
        copies — the file header, the method docstrings, the MCP tool
        docstrings, and the mirror md.
        """
        return [
            "## Talking to people and to other agents",
            "",
            "You are not alone. Two kinds of conversation reach you, and the top "
            "of each turn tells you which one you are in and how to answer it.",
            "",
            f"Your agent ID: `{self.agent_id}`",
            "",
            "### Private messages with another agent",
            "",
            "- `message_agent(to=<agent id>, text=...)` — write to one peer. The "
            "same call whether you are answering someone who just wrote to you "
            "or starting something yourself; messaging a peer is one act.",
            "- `to` is required. A turn can involve several peers, so the "
            "platform does not guess which one you mean. Who wrote to you is "
            "named at the top of the turn; everyone else you can reach is in "
            "your Known Agents list.",
            "- `find_agent(query)` finds peers by what they can do, for when the "
            "one you need is not in that list yet.",
            "- Writing to an agent starts a full turn for them, and their answer "
            "arrives as a NEW turn — not inside this one. So write when you have "
            "something for them, and do not wait around for a reply.",
            "",
            "### Team rooms",
            "",
            "A team is a shared room: you, your teammates, and your owner all see "
            "everything in it.",
            "",
            "- `message_team(team_id=..., text=...)` — say something in a room.",
            "- `team_id` is required: you can belong to several teams, so the "
            "platform does not pick one for you. The room a turn is about is "
            "named at the top of it.",
            "- `create_team(name, members)` — start a new team when work needs "
            "more than two people. It becomes a real room your owner can see, "
            "not a private side-channel.",
            "- `team_share_file` puts one of your files in the team's shared "
            "folder; `team_list_files` says what is already there. Both beat "
            "describing a file nobody can open.",
            "- `team_pin_rule` fixes a convention that should govern FUTURE "
            "replies (an output format, where files go) so nobody has to repeat "
            "it. Findings and status belong in the conversation, not in the rules.",
            "- The board — `team_work_add`, `team_work_list`, `team_work_claim`, "
            "`team_work_complete`, `team_work_update_status` — is how work "
            "outlives a turn. A task that exists only in a message is a task "
            "nobody can notice has stalled, including you, because this turn's "
            "memory is gone by the next one.",
            "",
            "### @mentions decide who WAKES UP, not who can see",
            "",
            "In a room, only @-mentioned agents are activated: @mentioning a "
            "teammate starts a full turn for them, and a message that names "
            "nobody wakes nobody. That is how work is handed on, so do it "
            "deliberately and not as a reflex. Say @all only when you genuinely "
            "need everyone.",
            "",
            "What you can READ in a given room is a property of that room, and "
            "its own prompt states it. This block only speaks to activation, "
            "which works the same way everywhere.",
            "",
            "When agents have passed something back and forth several times with "
            "no human word in between, the platform stops relaying @mentions and "
            "says so in the room. That is a fact about the room, not a rule for "
            "you to enforce: you will be told who was not reached.",
            "",
            "### Reading what you were given",
            "",
            "Each entry in your unread list is tagged with who sent it and where, "
            f"e.g.: `{_bus_tag('agent_xxx', 'ch_yyy')}` — sender first, then the "
            "conversation.",
            "- A sender shown as `User` is a **PERSON**, not an agent. A person "
            "can be in these conversations: a team room carries its owner's own "
            "messages. So read the sender rather than assuming a machine.",
            f"- A sender shown as `{SYSTEM_SENDER_LABEL}` is the PLATFORM "
            "narrating something it did — not a teammate, and not someone to "
            "answer or @mention.",
            "- These tags describe your unread QUEUE. They say nothing about what "
            "started THIS turn — that is stated at the top of the turn, so do not "
            "infer it from the presence or absence of a tag here.",
            "",
            "### Looking things up",
            "",
            "- Your unread messages and the current conversation are already in "
            "this turn's context. You do not need to fetch them.",
            "- `read_history` is for going back FURTHER than what you were given "
            "— when the answer depends on something older than this turn shows.",
            "- There is no registration tool. What peers see of you is rebuilt "
            "from your profile and your installed skills every turn; change it "
            "with `update_agent_profile` (Awareness). Capabilities are derived, "
            "never self-declared.",
            "",
            "### Autonomy — be proactive",
            "",
            "You are expected to handle what reaches you **autonomously**, not as "
            "a passive intermediary.",
            "- Another agent asks something you can answer → answer it. Do NOT "
            "ask your owner for permission first.",
            "- Another agent asks for help you can give → do it and say what "
            "came of it.",
            "- Treat these as real work, exactly like a request from your owner.",
            "- Escalate to your owner only when the request is outside what you "
            "can do, needs their approval, or involves a sensitive decision.",
            "",
            "### Reply Discipline — CRITICAL (prevents infinite loops)",
            "",
            "Autonomy does not mean answering everything. Every message you send "
            "starts a turn for someone, which can cascade. So:",
            # The species claim is CONDITIONAL. A team room carries its owner's
            # own messages, so a flat "the other party is a machine" aims the
            # skip-the-pleasantries rule at a person.
            "- **When the other party is another agent, it is not a human.** Skip "
            "pleasantries and warm-up phrases. Brevity beats politeness — one "
            "sentence beats three, and a single number or status word beats a "
            "sentence wrapped around it. Agents do not need to feel acknowledged. "
            "When the sender is a PERSON, talk to them like one.",
            "- **Silence when the thread is done.** If the other party only "
            "acknowledged ('thanks', 'got it', '好的'), do not answer again. The "
            "conversation reached its end.",
            "- **Do NOT ping-pong.** Once you have answered and they have "
            "acknowledged, stop. Another message adds nothing and starts another "
            "turn.",
            # 2026-08-03: 小雀 relayed its owner's question, 羽书 classified it as
            # "just forwarding" and stayed silent — so the human who asked never
            # got an answer. Silence is for ACKNOWLEDGEMENTS, never for questions.
            "- **A question is never ping-pong — answer it.** Including one "
            "somebody is relaying on their owner's behalf ('X wants to know what "
            "you are working on'): that a message is 'just forwarded' is NOT a "
            "reason for silence, because a human is waiting at the other end of "
            "it. And "
            "reporting the same thing to YOUR owner does not discharge the request "
            "— the agent who asked cannot see what you told your owner.",
            # 2026-08-01 briefing squad: five analysts did real research and ended
            # their turns with the results as plain text. Nothing was delivered.
            "- **Finished work is never ping-pong — deliver it.** When you "
            "complete something someone asked for (research, an answer, a "
            "document), it has to REACH them — and it only does so through the "
            "send call this turn offers you, which the top of the turn names. A "
            "turn that ends with the work sitting in your own reasoning "
            "delivered nothing. (No tool name is given here on purpose: which "
            "one delivers depends on where you are, and only the turn knows.)",
            "- **Do NOT repeat yourself.** If you have already said X, do not "
            "rephrase X to fill space.",
            "- **Substance only.** Write when you have new information, a "
            "concrete answer, a real question, or a result. Not 'I'm thinking "
            "about it', 'got your message', 'will get back to you'.",
            "- **If there is no substance, choose silence explicitly** — make no "
            "send call to that conversation at all. Silence is producing NOTHING, "
            "not producing something short. It is silence toward the PEER OR ROOM "
            "only: reporting to your owner is a separate act and this never "
            "suppresses it.",
            "- **An unanswered private message comes back.** A peer's message you "
            "choose not to answer stays unread and appears again next turn, so "
            "you can defer without forgetting. (A room works differently, and its "
            "own prompt says how.)",
            "",
            "### When your owner asks about your messages",
            "",
            "If they ask 'what messages do you have' or 'check your inbox', "
            "report what is there. That is a status question, not permission to "
            "start answering peers.",
            "",
            # P1 2026-08-02: agents answered "I can't do that" to "ask X what
            # they're working on". The capability was always there — the model
            # reached for a contact-lookup tool, hit an error, and gave up.
            "### When your owner asks you to find something out FROM another agent",
            "",
            "'Ask the teaching expert what they're working on', 'check whether X "
            "finished', 'find out if Y needs help' — these are things you CAN do. "
            "**Never answer that you are unable to reach another agent.**",
            "",
            "1. Find them in your Known Agents list (or with `find_agent` if they "
            "are not there yet) and use that exact id.",
            "2. Ask: `message_agent(to=<their id>, text=<your owner's question>)`. "
            "Do NOT use social-network or contact-lookup tools for this — those "
            "return contact details, not answers.",
            "3. Tell your owner you have asked and will report back. Their reply "
            "is a NEW turn, not something you wait for inside this one.",
            "4. When it arrives, relay the substance to your owner with "
            "`notify_owner`. That relay is the point of the "
            "errand — do not drop it. Reply discipline governs what you send to "
            "the AGENT; it never suppresses reporting back to your owner.",
            "",
            "If you genuinely cannot find them, say who you looked for and ask "
            "your owner which agent they meant — that is a clarifying question, "
            "not a refusal.",
        ]

    def _volatile_context_parts(self, ctx_data: ContextData) -> list:
        """The three live data lists (Known Agents / Your Channels / Unread
        Messages) — changed mid-session by bus tools and consumed unreads,
        so they are per-turn volatile. Rendering (caps, order, wording) is
        unchanged from the pre-R4 in-instruction rendering; only the
        destination differs by the relocation flag (R4: relocated, never
        dropped)."""
        parts = []

        # Known agents (capped + filtered)
        known = ctx_data.extra_data.get("bus_known_agents", [])
        if known:
            parts.append("")
            parts.append(f"### Known Agents (top {min(len(known), MAX_KNOWN_AGENTS_IN_CONTEXT)})")
            for a in known[:MAX_KNOWN_AGENTS_IN_CONTEXT]:
                name = a.get("agent_name") or a.get("agent_id", "")
                desc = a.get("agent_description") or a.get("description", "")
                aid = a.get("agent_id", "")
                line = f"- `{aid}` — {name}"
                # An unset description is rendered as NOTHING, never as the
                # creation placeholder: printing "a new agent ready for
                # configuration" next to every peer is what left "ask the
                # teaching expert" with nothing to aim at, and made this list
                # read as "none of these agents are usable" (P1 section 02).
                if not is_agent_description_unset(desc):
                    line += f": {desc[:80]}"
                # `via_team` was computed for every peer and read by nobody.
                # This list mixes teammates with every other agent the owner
                # has, so an agent reaching for help could not tell "already in
                # a room with me" from "a stranger I would have to DM cold".
                if a.get("via_team"):
                    line += " (teammate)"
                parts.append(line)

        # Channels (capped)
        channels = ctx_data.extra_data.get("bus_channels", [])
        if channels:
            parts.append("")
            parts.append(f"### Your Channels (top {min(len(channels), MAX_CHANNELS_IN_CONTEXT)})")
            for ch in channels[:MAX_CHANNELS_IN_CONTEXT]:
                cid = ch.get("channel_id", "")
                cname = ch.get("name", "unnamed")
                ctype = ch.get("channel_type", "group")
                parts.append(f"- `{cid}` — {cname} ({ctype})")

        # Unread messages (capped, with source tag preview)
        unread = ctx_data.extra_data.get("bus_unread_messages", [])
        if unread:
            # The window is what we render; the total is what the reader needs
            # in order to know a window is what it is looking at. They stopped
            # being the same number when the cap moved into the query.
            shown = len(unread)
            total = int(ctx_data.extra_data.get("bus_unread_total") or shown)
            parts.append("")
            parts.append(f"### Unread Messages: {total} (showing {shown})")
            # Same scoping as the static rule above, for the same reason —
            # and it matters more here, because this header sits directly on a
            # list that MIXES team-room messages in. A team room clears its
            # cursor once a turn has rendered it, so "ignored messages stay
            # unread" is false for part of what is printed underneath.
            parts.append(
                "> Remember: apply Reply Discipline. A direct message you do "
                "not answer stays unread; what a room keeps unread is stated "
                "by that room's own prompt."
            )
            for m in unread[:MAX_UNREAD_IN_CONTEXT]:
                content = (m.get("content") or "")[:200]
                # `msg_type` is carried so platform lines are labelled rather
                # than quoted as a member. This list has no type filter (and
                # neither does the unread predicate), so patrol lines, stop and
                # bulletin notices are unread for every member and land here —
                # the room's own prompt labels them `[system]`, and until now
                # this list printed them as a teammate with a synthetic name.
                tag = _bus_tag(
                    m.get("from_agent"), m.get("channel_id", ""), m.get("msg_type")
                )
                parts.append(f"- `{tag}` {content}")

        return parts

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """Usage rules, plus (flag OFF only) the live data lists.

        With the R4 relocation flag ON the output is byte-stable across
        turns and the lists travel via get_turn_context(); flag OFF keeps
        the legacy single-block rendering, byte-identical to pre-R4.
        """
        parts = self._static_instruction_parts()
        if not settings.prompt_turn_context_relocation_enabled:
            parts = parts + self._volatile_context_parts(ctx_data)
        return "\n".join(parts)

    async def get_turn_context(self, ctx_data: ContextData) -> str:
        """Per-turn volatile span: the Known Agents / Your Channels /
        Unread Messages lists, under a stable heading."""
        volatile = self._volatile_context_parts(ctx_data)
        if not volatile:
            return ""
        return "\n".join(["### Who is around, and what is waiting", *volatile])

    # =========================================================================
    # Hooks
    # =========================================================================

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """
        Inject MessageBus context into agent data.

        1. Auto-register current agent in bus_agent_registry (idempotent)
        2. Fetch known agents (filtered + capped)
        3. Fetch unread messages (capped)
        4. Fetch channel list (capped)
        5. (nothing) — a step here used to prefix the turn's input with a
           source tag. It was deleted on 2026-08-17 after it turned out never
           to have executed; see the comment where it stood, at the end of this
           method, which names both dead keys. Do not describe an input prefix
           in this contract again without first checking that one exists.
        """
        try:
            bus = await _get_default_bus_async()
            if bus is None:
                return ctx_data

            # --- 1. Keep this agent's peer-discovery row true ---
            #
            # Was an inline write with `capabilities=[]` hardcoded and the raw
            # `agent_description` (i.e. the creation placeholder) republished —
            # which is what made `bus_search_agents` answer nothing for every
            # query and told askers that a configured peer was "a new agent
            # ready for configuration" (P1 section 02, all 488 prod rows). The policy
            # now lives in ONE service that creation / rename / config / skill
            # install also call, so discovery no longer waits for a first turn;
            # this call is the idempotent per-turn backstop.
            try:
                db = await _get_shared_db()
                if db:
                    from xyz_agent_context.message_bus.agent_discovery_sync import (
                        sync_agent_discovery,
                    )
                    await sync_agent_discovery(db, self.agent_id)
            except Exception as e:
                logger.debug(f"Failed to sync agent discovery row: {e}")

            # --- 2. Fetch known agents ---
            #
            # Visibility rule (subproject 1, 议题 7 follow-up):
            #
            #   * If THIS agent belongs to one or more teams → only same-team
            #     members (across all of this agent's teams) + public agents.
            #     Same-owner agents NOT in any of my teams are excluded —
            #     team is the messageable boundary once you've opted in.
            #
            #   * If this agent is in NO team → fall back to legacy behavior:
            #     same-owner agents + public. (Backwards-compatible default
            #     for users who haven't created any team.)
            #
            # This way "team" actually scopes who an agent can talk to via
            # the bus; users who don't use teams keep the old "everyone I own"
            # behavior unchanged.
            known_agents = []
            try:
                db = await _get_shared_db()
                if db:
                    agent_row = await db.get_one("agents", {"agent_id": self.agent_id})
                    my_owner = agent_row.get("created_by", "") if agent_row else ""

                    # Resolve my team-mates (set of agent_ids I share at least
                    # one team with). Empty set → I'm in no team → legacy mode.
                    from xyz_agent_context.repository import TeamMemberRepository
                    team_repo = TeamMemberRepository(db)
                    my_team_ids = await team_repo.list_teams_by_agent(self.agent_id)
                    teammate_ids: set = set()
                    if my_team_ids:
                        for mate in await team_repo.list_team_mates(self.agent_id):
                            teammate_ids.add(mate["agent_id"])

                    use_team_scope = bool(my_team_ids)

                    all_agents = await db.get("agents", {})
                    for a in all_agents:
                        aid = a.get("agent_id")
                        if aid == self.agent_id:
                            continue
                        same_owner = my_owner and a.get("created_by") == my_owner
                        is_public = bool(a.get("is_public", 0))
                        if use_team_scope:
                            # In team mode: only team-mates (same owner or not)
                            # plus public agents.
                            if aid not in teammate_ids and not is_public:
                                continue
                        else:
                            # Legacy / no-team mode: same owner OR public.
                            if not (same_owner or is_public):
                                continue
                        known_agents.append({
                            "agent_id": aid,
                            "agent_name": a.get("agent_name", ""),
                            "agent_description": a.get("agent_description", ""),
                            "is_public": a.get("is_public", 0),
                            "created_by": a.get("created_by", ""),
                            # Whether this agent shares a team with us —
                            # rendered as "(teammate)" in the list below.
                            "via_team": aid in teammate_ids,
                        })
                        if len(known_agents) >= MAX_KNOWN_AGENTS_IN_CONTEXT:
                            break
                if known_agents:
                    ctx_data.extra_data["bus_known_agents"] = known_agents
            except Exception as e:
                logger.debug(f"Failed to fetch known agents: {e}")

            # --- 3. Fetch unread messages (capped) ---
            # The cap is pushed into the query, and it selects the NEWEST ones.
            # Slicing an oldest-first list in Python (what this used to do) kept
            # handing the model the most ancient messages in the backlog — and
            # since the read cursor never advanced in team rooms, that window
            # was frozen: the same 20 lines, turn after turn, described as if
            # they were the room's current state.
            try:
                unread = await bus.get_unread(
                    self.agent_id, limit=MAX_UNREAD_IN_CONTEXT
                )
                if unread:
                    ctx_data.extra_data["bus_unread_messages"] = [
                        msg.model_dump() for msg in unread
                    ]
                    # The total is a separate question once the fetch is capped:
                    # len() of a window always equals the window.
                    ctx_data.extra_data["bus_unread_total"] = await bus.count_unread(
                        self.agent_id
                    )
            except Exception as e:
                logger.debug(f"Failed to fetch unread messages: {e}")

            # --- 4. Fetch channels (capped) ---
            try:
                rows = await bus._db.execute(
                    "SELECT c.* FROM bus_channels c "
                    "JOIN bus_channel_members cm ON c.channel_id = cm.channel_id "
                    "WHERE cm.agent_id = %s "
                    "ORDER BY c.created_at DESC "
                    "LIMIT %s",
                    (self.agent_id, MAX_CHANNELS_IN_CONTEXT),
                )
                if rows:
                    ctx_data.extra_data["bus_channels"] = [dict(r) for r in rows]
            except Exception as e:
                logger.debug(f"Failed to load bus channels: {e}")

            # --- 5. (removed) input source tag ---
            #
            # A branch used to sit here prefixing `[MessageBus · sender ·
            # channel]` onto the turn's input. It never ran, and could not have
            # worked if it had:
            #
            #   * its guard read `extra_data["working_source"]`, and nothing in
            #     the repo writes that key — `working_source` is a ContextData
            #     FIELD, seeded at `context_runtime.py:147`, while `extra_data`
            #     is filled from `trigger_extra_data` alone (the correct read is
            #     `working_source_matches(ctx_data.working_source, …)`, which
            #     `get_expressive_tools` in this same file already does);
            #   * it wrote to `extra_data["input_content"]`, which had no reader
            #     in the codebase. The input the model actually receives is the
            #     FIELD `ctx_data.input_content` (`context_runtime.py:1032`).
            #
            # It is deleted rather than repaired because switching it on is a
            # live change to two shipped prompts — a team turn's input IS the
            # whole `[Team Group Chat]…` prompt and a DM turn's is
            # `_build_prompt`'s output, and both already name their sender in
            # their own words, so a machine prefix in front of them is a product
            # decision, not a bug fix. The instructions above now describe the
            # tags that genuinely reach the model, on the unread list.

        except Exception as e:
            logger.exception(f"MessageBusModule hook_data_gathering failed: {e}")
        return ctx_data

    async def hook_after_event_execution(
        self, params: HookAfterExecutionParams
    ) -> None:
        """
        Post-execution cleanup for MessageBus.

        Selective mark_read: only mark messages as read if the agent actually
        replied to them. Messages the agent ignored stay unread and will
        resurface on the next turn — this is the "silence is acceptable"
        mechanism.

        We detect replies by inspecting the trace for `message_agent` and
        `message_team` calls. Those names have to track the tools: while they
        still named the pre-2026-08-17 tools, nothing counted as a reply and this
        cursor stopped advancing altogether.
        """
        # Only process if this was a bus-triggered execution OR if the agent
        # actually sent bus messages this turn (could be user-initiated outreach)
        try:
            bus = await _get_default_bus_async()
            if bus is None:
                return

            # Extract channel IDs that were replied to
            replied_channels: set[str] = set()
            replied_agents: set[str] = set()   # peers written to via message_agent
            replied_teams: set[str] = set()    # rooms spoken in via message_team

            if params.trace and params.trace.agent_loop_response:
                for response in params.trace.agent_loop_response:
                    tool_name = getattr(response, "tool_name", None)
                    tool_input = getattr(response, "tool_input", None)

                    # Names arrive either fully qualified
                    # (mcp__message_bus_module__message_agent) or bare, depending
                    # on how the SDK reports them, so match on the substring.
                    #
                    # 2026-08-17 — these MUST track the tool renames. They were
                    # still matching `bus_send_message` / `bus_send_to_agent`
                    # after both tools were replaced, which meant nothing ever
                    # counted as a reply and this cursor never advanced again:
                    # the same permanently-unread deadlock just fixed on the IM
                    # side, re-introduced on the peer side by a rename.
                    if not tool_name:
                        continue
                    if not isinstance(tool_input, dict):
                        continue

                    if "message_team" in tool_name:
                        # The room's channel follows from the team, and the hook
                        # does not have it — but every unread row in that room is
                        # in the same channel as the reply, so record the team and
                        # resolve it below.
                        tid = tool_input.get("team_id")
                        if tid:
                            replied_teams.add(tid)
                    elif "message_agent" in tool_name:
                        target = tool_input.get("to")
                        if target:
                            replied_agents.add(target)

            # A team we spoke in resolves to its room, deterministically: the
            # group channel whose created_by is the team marker.
            if replied_teams:
                from xyz_agent_context.schema.team_schema import (
                    TEAM_ROOM_OWNER_PREFIX,
                )

                db = await _get_shared_db()
                for tid in replied_teams:
                    row = await db.get_one(
                        "bus_channels",
                        {"created_by": f"{TEAM_ROOM_OWNER_PREFIX}{tid}",
                         "channel_type": "group"},
                    ) if db else None
                    if row and row.get("channel_id"):
                        replied_channels.add(row["channel_id"])

            # Only mark read for channels where we actually replied
            if not replied_channels and not replied_agents:
                logger.debug(
                    f"MessageBus: agent {self.agent_id} did not reply to any bus "
                    f"messages this turn — unread messages stay unread"
                )
                return

            # Get all unread and filter to only the replied-to conversations
            unread = await bus.get_unread(self.agent_id)
            if not unread:
                return

            to_mark = []
            for m in unread:
                if m.channel_id in replied_channels:
                    to_mark.append(m.message_id)
                    continue
                # For bus_send_to_agent: we sent a DM to some agent. Mark read
                # any unread DM from that same agent (the DM channel includes both).
                if m.from_agent in replied_agents:
                    to_mark.append(m.message_id)

            if to_mark:
                await bus.mark_read(self.agent_id, to_mark)
                logger.info(
                    f"MessageBus: selective mark_read — {len(to_mark)}/{len(unread)} "
                    f"messages marked read for agent {self.agent_id} "
                    f"(replied to {len(replied_channels)} channels, {len(replied_agents)} DMs)"
                )
            else:
                logger.debug(
                    f"MessageBus: agent {self.agent_id} replied to channels "
                    f"{replied_channels} but no matching unread messages to mark"
                )
        except Exception as e:
            logger.exception(f"MessageBusModule hook_after_event_execution failed: {e}")


# =============================================================================
# Module-level helpers
# =============================================================================

_bus_instances: dict[int, Any] = {}  # keyed by event loop id


async def _get_default_bus_async():
    """Get or create a LocalMessageBus bound to the current event loop.

    Uses ``get_db_client()`` which already handles event-loop changes and
    creates a fresh aiomysql pool on the correct loop.  The bus instance is
    cached per-loop so subsequent calls on the same loop are free.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    loop_id = id(loop)

    if loop_id in _bus_instances:
        return _bus_instances[loop_id]

    try:
        from xyz_agent_context.message_bus import LocalMessageBus
        from xyz_agent_context.utils.db.db_factory import get_db_client

        db = await get_db_client()
        backend = db._backend
        if backend is None:
            logger.warning("MessageBus: database backend is None")
            return None

        bus = LocalMessageBus(backend=backend)
        _bus_instances[loop_id] = bus
        logger.info(f"MessageBus: created instance for event loop {loop_id}")
        return bus
    except Exception as e:
        logger.exception(f"Failed to initialize default MessageBus: {e}")
        return None


def _get_default_bus():
    """Sync wrapper — only works if a bus was already created for the current loop."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        return _bus_instances.get(id(loop))
    except RuntimeError:
        return None


async def _get_shared_db():
    """Get the shared AsyncDatabaseClient."""
    try:
        from xyz_agent_context.utils.db.db_factory import get_db_client
        return await get_db_client()
    except Exception as e:
        logger.debug(f"Failed to get shared DB client: {e}")
        return None
