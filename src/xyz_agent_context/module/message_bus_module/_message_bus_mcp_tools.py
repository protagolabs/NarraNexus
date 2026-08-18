"""
@file_name: _message_bus_mcp_tools.py
@author: Bin Liang
@date: 2026-04-02
@description: MCP atomic tools for the agent's messaging surface.

These tools map to the MessageBusService interface methods.
The bus_ prefix avoids collision with lark_*, slack_*, etc.

Each tool receives a get_message_bus_fn async callable that returns a
MessageBusService instance, following the project pattern for
dependency injection in MCP tool modules.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from xyz_agent_context.schema import BUS_ERRAND_TURN_SOURCE, WorkingSource

# Both send tools stamp WHICH KIND of turn is sending: an owner-facing turn
# means this is an errand question (the recipient must answer US), a
# message_bus turn means we are already answering a peer (so their next
# message is a reply). MessageBusTrigger picks the recipient's directive from
# it, and both tools write the same table — so both must record it.
from xyz_agent_context.module._mcp_identity import (
    caller_errand_scope,
    caller_event_id_from_request,
    caller_root_run_id,
    caller_team_id_from_request,
    caller_turn_source,
)


async def _describe_agent(agent_id: str) -> str:
    """``"Name (agent_id)"`` for echoing a recipient back to the sender.

    The echo exists so a wrong `to` shows up in the SAME turn instead of
    surfacing later as a confused answer from somebody the agent never meant to
    write to.

    **Never raises, and that is load-bearing.** It runs after the send has
    already succeeded, inside the tool's `try`; an exception here would report
    `success: false` for a message that WAS delivered — the agent would then
    reasonably send it again. A cosmetic label must not be able to invert the
    outcome of the action it labels.
    """
    try:
        from xyz_agent_context.utils.db.db_factory import get_db_client

        db = await get_db_client()
        row = await db.get_one("agents", {"agent_id": agent_id})
        name = (row or {}).get("agent_name") or ""
        return f"{name} ({agent_id})" if name else agent_id
    except Exception:  # noqa: BLE001 — see docstring: never invert the outcome
        return agent_id


def _send_turn_source(*, to_agent: str = "", channel_id: str = "") -> Optional[str]:
    """The ``sender_turn_source`` stamp for ONE send.

    Starts from the turn kind, then upgrades to ``BUS_ERRAND_TURN_SOURCE`` for
    the one case the turn kind cannot express: a bus turn that is continuing
    OUR errand, sending to the peer/channel that errand lives in. Such a
    message is a QUESTION even though the turn is "message_bus", so the
    recipient must not read it as an answer and relay it to its owner (P1
    evt_0dcee899, path A of the 2026-08-03 review).

    Why per-send and not per-turn: a bus turn is not homogeneous. Unread bus
    messages are injected from ALL channels every turn
    (``MessageBusModule.hook_data_gathering`` → ``bus.get_unread``), and the
    module prompt REQUIRES answering them ("A question is never ping-pong").
    So an errand-continuation turn routinely also answers an unrelated peer C.
    Stamping the whole turn made C's answer look like a question, and C — which
    had asked on its own owner's behalf — stopped relaying to that owner: the
    very P1 failure, one seat over (2026-08-03 review). The scope check keeps
    the upgrade on the errand and leaves every other send plain.

    Residuals, documented (the full list, with the recipient-side ones, lives
    on ``MessageBusTrigger._incoming_is_reply_to_my_errand``): answering the
    errand peer's OWN question inside our errand turn also gets the errand
    stamp (needs mutual errands in flight in the same reused DM channel), and a
    send into a GROUP channel that happens to be the errand channel stamps
    every member. Both are narrower than what whole-turn stamping broke.
    """
    source = caller_turn_source()
    if source != WorkingSource.MESSAGE_BUS.value:
        # Owner-facing turns are already unambiguous questions; None means the
        # transport told us nothing and the recipient degrades on its own.
        return source

    errand_peer, errand_channel = caller_errand_scope()
    aimed_at_errand = (
        (bool(to_agent) and to_agent == errand_peer)
        or (bool(channel_id) and channel_id == errand_channel)
    )
    return BUS_ERRAND_TURN_SOURCE if aimed_at_errand else source


def _split_refs(refs: str) -> List[str]:
    """Parse a comma-separated attachment_refs string into a clean list."""
    return [r.strip() for r in (refs or "").split(",") if r.strip()]


async def _resolve_owner_user_id(agent_id: str) -> Optional[str]:
    """Look up an agent's owning user_id (agents.created_by), dialect-safe."""
    from xyz_agent_context.utils.db.db_factory import get_db_client
    db = await get_db_client()
    row = await db.get_one("agents", {"agent_id": agent_id})
    return row.get("created_by") if row else None


async def _stage_send_attachments(agent_id: str, refs: str) -> List[dict]:
    """Resolve + stage attachment_refs for a sending agent into the shared bus
    area. Returns [] when there are no refs or the owner can't be resolved."""
    ref_list = _split_refs(refs)
    if not ref_list:
        return []
    owner = await _resolve_owner_user_id(agent_id)
    if not owner:
        return []
    from xyz_agent_context.message_bus.attachments import resolve_and_stage_refs
    return await resolve_and_stage_refs(
        sender_agent_id=agent_id, owner_user_id=owner, refs=ref_list
    )


#: What the agent is told when the messaging backend is down. It never names
#: the subsystem: "MessageBus not available" asks the model to reason about a
#: component it has no model of, and the only useful next step — stop trying to
#: send this turn — is the same either way.
_UNAVAILABLE = "messaging is temporarily unavailable — do not retry this turn"


async def _resolve_conversation(
    agent_id: str, *, with_agent: str, team_id: str
) -> tuple[Optional[str], Optional[str]]:
    """One conversation handle -> (channel_id, error). Never raises for "not found".

    Membership is enforced BY the query rather than checked after it: the DM
    lookup joins on both members, so a channel the caller does not belong to
    cannot come back, and the team lookup requires a `team_members` row. A
    resolver that found the channel first and authorised second would be one
    forgotten branch away from letting an agent read a conversation it is not in.

    `%s` rather than `db.placeholder`: callers hold an `AsyncDatabaseClient`,
    which translates `%s` per dialect and has no `.placeholder` attribute at all
    (the bug that took every `message_team` down — see `team_posting`).
    """
    from xyz_agent_context.utils.db.db_factory import get_db_client

    db = await get_db_client()
    if team_id:
        member = await db.get_one(
            "team_members", {"team_id": team_id, "agent_id": agent_id}
        )
        if not member:
            return None, f"you are not in team {team_id}"
        from xyz_agent_context.message_bus.team_rooms import primary_room_of

        channel_id = await primary_room_of(db, team_id)
        if not channel_id:
            return None, f"team {team_id} has no room yet"
        return channel_id, None

    rows = await db.execute(
        "SELECT c.channel_id FROM bus_channels c "
        "JOIN bus_channel_members m1 ON c.channel_id = m1.channel_id AND m1.agent_id = %s "
        "JOIN bus_channel_members m2 ON c.channel_id = m2.channel_id AND m2.agent_id = %s "
        "WHERE c.channel_type = 'direct'",
        (agent_id, with_agent),
        fetch=True,
    )
    if not rows:
        return None, f"you have no private conversation with {with_agent} yet"
    return rows[0]["channel_id"], None


def register_message_bus_mcp_tools(
    mcp: Any,
    get_message_bus_fn: Callable,
) -> None:
    """
    Register all MessageBus MCP tools on the given MCP server instance.

    Called by MessageBusModule.create_mcp_server().

    Args:
        mcp: The FastMCP server instance.
        get_message_bus_fn: Async callable that returns a MessageBusService instance.
    """

    @mcp.tool()
    async def message_agent(
        agent_id: str,
        to: str,
        text: str,
        attachment_refs: str = "",
    ) -> dict:
        """
        Send a private message to another agent.

        The same action whether you are answering someone who just wrote to you
        or starting a conversation of your own — messaging a peer is one act,
        so it is one tool.

        Args:
            agent_id: your own agent id (the sender)
            to: the agent id of the person you are writing to. REQUIRED — a
                turn can involve several peers, so the platform does not guess.
                Who is talking to you right now is stated at the top of this
                turn; other agents you can reach are in your Known Agents list.
            text: what to say
            attachment_refs: comma-separated file handles to attach. Each is
                either an attachment file_id ("att_...") you received, or a path
                to a file in your own workspace ("work/report.pdf"). Files are
                shared by reference — the recipient opens them with Read — so
                attach freely. Same-user agents only.

        Sending to someone triggers a full turn for them, so send with intent.
        The reply arrives as a new turn, not inside this one.

        Returns:
            {"success": true, "message_id": ..., "sent_to": "<name> (<id>)"}
            The recipient is echoed back so a mistake is visible in the same
            turn instead of surfacing as a confused answer later.
        """
        bus = await get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": _UNAVAILABLE}
        if not (to or "").strip():
            return {
                "success": False,
                "error": "`to` is required — name the agent you are writing to.",
            }

        if not (text or "").strip():
            # The routing argument was guarded from the start and the CONTENT was
            # not, which is the more consequential half. Blank text posts an empty
            # bubble into a surface a person reads, and then
            # `has_message_from_turn` answers True for the turn — so the
            # "said nothing" notice is suppressed and the turn files as delivered.
            # A room that looks answered and says nothing is strictly worse than
            # the silence it replaced, which at least produced a notice. On the
            # peer lane it is worse still: an empty message starts a full LLM turn
            # for the recipient with nothing to answer.
            #
            # An ERROR, not a silent no-op: a tool that returns success for a
            # no-op teaches the model it replied. Same discipline
            # `inbox_recorder.record_turn` already applies to an empty outbound
            # row, applied to the tool that is now every team turn's reply.
            return {
                "success": False,
                "error": "`text` is empty — say something, or end the turn "
                         "without calling this.",
            }

        try:
            attachments = await _stage_send_attachments(agent_id, attachment_refs)
            msg_id = await bus.send_to_agent(
                from_agent=agent_id,
                to_agent=to.strip(),
                content=text,
                attachments=attachments or None,
                sender_turn_source=_send_turn_source(to_agent=to.strip()),
                # Carry this turn's trigger tree onto the message: the run this
                # wakes has no other way to learn which tree it continues, and a
                # broken lineage means a cascade stop leaves that branch running.
                root_run_id=caller_root_run_id(),
                # WHICH turn produced this message. A column whose meaning
                # depends on which tool wrote the row is a column nobody can
                # query. Absent header degrades to None by design; the consumer
                # reads a missing id as "cannot tell", never as "it happened".
                event_id=caller_event_id_from_request(),
            )
            return {
                "success": True,
                "message_id": msg_id,
                "sent_to": await _describe_agent(to.strip()),
                "attached": len(attachments),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def message_team(
        agent_id: str,
        team_id: str,
        text: str,
    ) -> dict:
        """
        Say something in a team room.

        The same action whether you are answering what was just said to you or
        raising something new — speaking in a room is one act, so it is one
        tool.

        Args:
            agent_id: your own agent id (the sender)
            team_id: which team's room to speak in. REQUIRED — you can belong to
                several teams, so the platform does not guess. The room this turn
                is about is named at the top of the turn; the teams you belong to
                are listed with it.
            text: what to say. Write it as you would in a group chat — no
                process narration, no tool names. To pull a teammate in,
                @mention them by name; say @all for everyone.

        @mentions wake the teammates you name, so use them deliberately: that is
        how work is handed on, and each one starts a full turn for someone.

        When agents have passed a thread back and forth several times with no
        human word in between, the platform stops relaying @mentions and says so
        in the room. `capped` then names who was NOT reached, so you can decide
        what to do instead of assuming they were told.

        Returns:
            {"success": true, "message_id": ..., "mentioned": [...],
             "capped": {"names": [...], "everyone": bool}}
        """
        bus = await get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": _UNAVAILABLE}
        if not (team_id or "").strip():
            return {
                "success": False,
                "error": "`team_id` is required — name the team room you are speaking in.",
            }

        if not (text or "").strip():
            # The routing argument was guarded from the start and the CONTENT was
            # not, which is the more consequential half. Blank text posts an empty
            # bubble into a surface a person reads, and then
            # `has_message_from_turn` answers True for the turn — so the
            # "said nothing" notice is suppressed and the turn files as delivered.
            # A room that looks answered and says nothing is strictly worse than
            # the silence it replaced, which at least produced a notice. On the
            # peer lane it is worse still: an empty message starts a full LLM turn
            # for the recipient with nothing to answer.
            #
            # An ERROR, not a silent no-op: a tool that returns success for a
            # no-op teaches the model it replied. Same discipline
            # `inbox_recorder.record_turn` already applies to an empty outbound
            # row, applied to the tool that is now every team turn's reply.
            return {
                "success": False,
                "error": "`text` is empty — say something, or end the turn "
                         "without calling this.",
            }

        try:
            from xyz_agent_context.message_bus.team_posting import post_team_reply
            from xyz_agent_context.message_bus.team_rooms import (
                primary_room_of,
                room_roster,
            )
            from xyz_agent_context.utils.db.db_factory import get_db_client

            team_id = team_id.strip()
            db = await get_db_client()

            # Same three gates every team tool uses, in the same order: the
            # agent exists, the team belongs to its owner, the agent is a member.
            agent_row = await db.get_one("agents", {"agent_id": agent_id})
            if not agent_row:
                return {"success": False, "error": "unknown agent"}
            team = await db.get_one("teams", {"team_id": team_id})
            if not team or team.get("owner_user_id") != agent_row.get("created_by"):
                return {"success": False, "error": "team not found for this owner"}
            # Checked against `team_members` — the source of truth for who
            # belongs to a team. NOT against the channel, which is the delivery
            # mirror and lags a roster edit until the next chat send or open.
            #
            # Once, verbatim: this was two identical queries with two identical
            # guards, i.e. two round-trips on every `message_team` for one fact.
            member = await db.get_one(
                "team_members", {"team_id": team_id, "agent_id": agent_id}
            )
            if not member:
                return {"success": False, "error": "you are not a member of this team"}

            # None → this team has never opened a room; posting would have to
            # create one as a side effect of sending a message, which is the
            # tail wagging the dog.
            channel_id = await primary_room_of(db, team_id) or ""
            if not channel_id:
                return {
                    "success": False,
                    "error": "this team has no room yet — it opens when the chat is first used",
                }

            # Roster drives @mention resolution, which matches on NAMES, so the
            # names have to be the ones teammates are shown by.
            roster = await room_roster(db, bus, channel_id)

            result = await post_team_reply(
                db=db,
                bus=bus,
                agent_id=agent_id,
                team_id=team_id,
                channel_id=channel_id,
                text=text,
                roster=roster,
                # WHICH turn produced this message, and which trigger tree it
                # continues. Absent headers degrade to None/"" by design.
                event_id=caller_event_id_from_request(),
                root_run_id=caller_root_run_id(),
            )
            return {"success": True, **result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def create_team(
        agent_id: str,
        name: str,
        members: str,
    ) -> dict:
        """
        Create a new team — a shared room you, your teammates and your owner
        can all see, and which appears in your owner's interface.

        Use this when work needs more than two participants. For talking to
        one peer, use `message_agent`; no room is needed for that.

        IMPORTANT: Always provide a meaningful channel name! Bad examples:
        "test", "channel", "untitled", "x". Good examples:
        "Project Alpha Coordination", "Q3 Sales Sync", "Customer Escalation - AcmeCorp".

        The agent_id (you) is automatically included as a member. Invited
        agents do NOT need to accept — they are added immediately and this
        call returns right away.

        Args:
            agent_id: Your agent ID (will be added as first member)
            name: Human-readable channel name describing purpose/topic
            members: Comma-separated agent IDs to invite (e.g. "agent_abc,agent_def")

        Returns:
            Result dict with channel_id on success
        """
        bus = await get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": _UNAVAILABLE}

        try:
            member_list = [m.strip() for m in members.split(",") if m.strip()]
            # Ensure the creator is included
            if agent_id not in member_list:
                member_list.insert(0, agent_id)

            channel_id = await bus.create_channel(
                name=name,
                members=member_list,
            )
            return {"success": True, "channel_id": channel_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def find_agent(
        agent_id: str,
        query: str,
    ) -> dict:
        """
        Search for agents you can reach by capability or description.

        Only agents in YOUR OWN account are returned — you cannot discover
        another user's agents via the bus. Use this when you need to find one
        of your own agents for a task and you don't already know their
        agent_id. If you already see the target in your "Known Agents" context
        list, use that agent_id directly — no search needed.

        Args:
            agent_id: Your agent ID (the searcher — scopes results to your account)
            query: Search query (matched against capabilities and description)

        Returns:
            Result dict with matching agents list
        """
        bus = await get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": _UNAVAILABLE}

        try:
            results = await bus.search_agents(query=query, requester_agent_id=agent_id)
            return {
                "success": True,
                "agents": [a.model_dump() for a in results],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def team_share_file(
        agent_id: str,
        team_id: str,
        file_path: str,
    ) -> dict:
        """
        Publish one of your files into a team's SHARED folder.

        Use this to hand a file to a whole team at once — a collaborative
        artifact, a report, a dataset — instead of attaching it to a single
        message. Every teammate can open the returned path with the Read tool.

        You can only share a file you actually have: a path in your own
        workspace (e.g. "work/plan.md") or an attachment file_id ("att_...").
        You must be a member of the team.

        Args:
            agent_id: Your agent ID
            team_id: The team to share into (you must be a member)
            file_path: A workspace-relative path OR an "att_" file_id

        Returns:
            Result dict with the shared absolute `path` on success. Announce
            that path in the team chat so teammates know it is there.
        """
        try:
            from xyz_agent_context.utils.db.db_factory import get_db_client
            from xyz_agent_context.message_bus.attachments import (
                stage_path_into_team,
            )

            db = await get_db_client()
            agent_row = await db.get_one("agents", {"agent_id": agent_id})
            if not agent_row:
                return {"success": False, "error": "unknown agent"}
            owner = agent_row.get("created_by")

            team = await db.get_one("teams", {"team_id": team_id})
            if not team or team.get("owner_user_id") != owner:
                return {"success": False, "error": "team not found for this owner"}
            membership = await db.get_one(
                "team_members", {"team_id": team_id, "agent_id": agent_id}
            )
            if not membership:
                return {"success": False, "error": "you are not a member of this team"}

            staged = await stage_path_into_team(
                sender_agent_id=agent_id,
                owner_user_id=owner,
                team_id=team_id,
                ref=file_path,
            )
            if staged is None:
                return {"success": False, "error": f"file not found: {file_path}"}
            return {"success": True, "path": staged["path"], "name": staged["original_name"]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def team_pin_rule(
        agent_id: str,
        content: str,
        tier: str = "long_term",
    ) -> dict:
        """
        Pin a standing rule onto the team bulletin, so it is never said twice.

        Every teammate loads the bulletin at the start of EVERY team turn, no
        matter how long ago it was written and no matter when they joined. That
        is what makes it different from saying something in the chat, which
        scrolls out of view after about twenty messages and is invisible to
        anyone who joins later.

        Pin something only when it should govern FUTURE replies: a convention
        the team settled on, an output format, a place files must go. Do not
        pin conversation, findings, status, or anything you would not want
        prepended to every teammate's next twenty turns. Space is small and
        shared with the user's own rules — if it is a fact rather than a rule,
        say it in the chat instead.

        You can remove a rule YOU pinned with team_unpin_rule. You cannot
        remove the user's rules; ask them.

        Args:
            agent_id: Your agent ID
            content: The rule, in one sentence
            tier: "long_term" for a standing rule, "current_task" for one that
                stops applying when this task is done

        Returns:
            Result dict with entry_id on success, or error details
        """
        try:
            from xyz_agent_context.utils.db.db_factory import get_db_client
            from xyz_agent_context.message_bus.team_bulletin import post_team_bulletin

            # The turn's team, from the server-side identity headers — never a
            # tool argument. An agent cannot name a team it is not currently
            # working in, so cross-team writes are not expressible rather than
            # merely forbidden.
            return await post_team_bulletin(
                db=await get_db_client(),
                agent_id=agent_id,
                team_id=caller_team_id_from_request() or "",
                content=content,
                tier=tier,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def team_unpin_rule(agent_id: str, entry_id: str) -> dict:
        """
        Remove a bulletin rule that YOU pinned earlier.

        Use this when a rule you added no longer holds — a superseded format, a
        finished task's setup. You can only remove your own; the user's rules
        and your teammates' are not yours to retract, and neither is the
        auto-generated team progress summary. Ask the user instead.

        Args:
            agent_id: Your agent ID
            entry_id: The bulletin entry id returned when you pinned it

        Returns:
            Result dict, or error details explaining why it was refused
        """
        try:
            from xyz_agent_context.utils.db.db_factory import get_db_client
            from xyz_agent_context.message_bus.team_bulletin import remove_team_bulletin

            return await remove_team_bulletin(
                db=await get_db_client(),
                agent_id=agent_id,
                team_id=caller_team_id_from_request() or "",
                entry_id=entry_id,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def team_list_files(agent_id: str, team_id: str) -> dict:
        """List the files shared into a team's folder — what the team has, not
        what someone happened to mention.

        Use this instead of guessing paths or asking a teammate to repeat one.
        Each entry gives the file's name, the absolute path you can open with
        Read, its size, and who shared it.

        You must be a member of the team. An empty list means nothing has been
        shared yet — that is an answer, not an error.

        Args:
            agent_id: Your own agent id.
            team_id: The team whose folder to list.
        """
        try:
            from xyz_agent_context.message_bus.team_files import list_team_files
            from xyz_agent_context.module._mcp_identity import resolve_caller_agent_id
            from xyz_agent_context.utils.db.db_factory import get_db_client

            db = await get_db_client()
            return await list_team_files(
                db=db, agent_id=resolve_caller_agent_id(agent_id), team_id=team_id
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def read_history(
        agent_id: str,
        with_agent: str = "",
        team_id: str = "",
        limit: int = 50,
    ) -> dict:
        """
        Look further back in one of your conversations.

        Name the conversation the way you would to write in it: `with_agent`
        for a private conversation, `team_id` for a team. Exactly one.

        Use it when you need how a discussion evolved and the messages you were
        given are not enough. Do NOT call it for a conversation whose recent
        messages are already in front of you, and do NOT call it in a loop.

        Args:
            agent_id: Your agent ID
            with_agent: the agent id of the peer, for a private conversation
            team_id: the team, for a team conversation
            limit: Maximum number of messages (default 50)
        """
        # Handles, not channel ids, and that is the point rather than a
        # convenience: an agent's world is a private conversation or a team,
        # and a tool that took a raw channel id would be the one place it had
        # to know otherwise — after which the id has to be printed into its
        # context to be usable, and the vocabulary is back.
        bus = await get_message_bus_fn()
        if bus is None:
            return {"success": False, "error": _UNAVAILABLE}

        with_agent = (with_agent or "").strip()
        team_id = (team_id or "").strip()
        if bool(with_agent) == bool(team_id):
            return {
                "success": False,
                "error": "name exactly one conversation: `with_agent` for a "
                         "private one, `team_id` for a team.",
            }

        try:
            channel_id, err = await _resolve_conversation(
                agent_id, with_agent=with_agent, team_id=team_id
            )
            if err:
                return {"success": False, "error": err}
            messages = await bus.get_messages(channel_id, limit=limit)
            return {"success": True, "messages": [
                {"from": m.from_agent, "content": m.content, "time": str(m.created_at), "mentions": m.mentions}
                for m in messages
            ]}
        except Exception as e:
            return {"success": False, "error": str(e)}

