"""
@file_name: test_visibility_wording.py
@author:
@date: 2026-08-11
@description: The module's standing rules must be true in every room it reaches.

`_static_instruction_parts` is emitted on every bus-enabled turn with no idea
what kind of room the turn is in — that is the point of it, and the R4 cache
depends on it staying byte-identical. So anything it asserts has to hold
everywhere, and two lines did not:

  * "In group channels, you only see messages that @mention you." True for an
    ordinary bus group. False in a team room, whose turn prompt carries the
    room's full recent scrollback and says so ten lines later. Two contradictory
    claims about the same room, in the same context window.
  * "Ignored messages resurface — they stay unread and appear again next turn."
    True in a DM, where the unread list is the queue. False in a team room since
    the read cursor started advancing on a rendered turn.

The fix is not a room-type branch — that would fork the static block and cost
the byte-stability it exists for. It is to say only what is true everywhere and
leave the room-specific fact to the room's own prompt, which is the only place
that knows.
"""
from __future__ import annotations

from xyz_agent_context.module.message_bus_module.message_bus_module import (
    MessageBusModule,
)
from xyz_agent_context.schema import WorkingSource


def _static_text() -> str:
    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    return "\n".join(module._static_instruction_parts())


def _async_return(value):
    """A stand-in coroutine function returning ``value``, ignoring arguments."""

    async def _fn(*_args, **_kwargs):
        return value

    return _fn


def test_the_static_rules_do_not_claim_mentions_limit_visibility():
    """Activation and visibility are different questions; this block may only
    speak to the first."""
    text = _static_text().lower()

    assert "you only see messages that @mention you" not in text


def test_activation_semantics_are_still_stated():
    """The part that IS true everywhere has to survive the rewrite — it is what
    stops an agent assuming a passive post woke somebody."""
    text = _static_text()

    assert "only @-mentioned agents are activated" in text


def test_resurfacing_is_scoped_to_where_it_happens():
    """A team room now clears its cursor once a turn has rendered the room, so
    an unqualified "ignored messages come back" is a promise the platform stops
    keeping the moment the agent is in a team."""
    text = _static_text()

    line = next(ln for ln in text.splitlines() if "resurface" in ln.lower())
    assert "direct" in line.lower() or "dm" in line.lower()


def test_a_teammate_is_marked_as_one_in_the_known_agents_list():
    """`via_team` was computed for every peer and read by nobody.

    The list mixes teammates with every other agent the owner has, and an agent
    reaching for help has no way to tell "we are on the same team, this one is
    already in the room with me" from "a stranger I would have to DM cold".
    """
    from xyz_agent_context.schema.context_schema import ContextData

    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    ctx = ContextData(agent_id="agent_me", user_id="usr_1", input_content="hi")
    ctx.extra_data["bus_known_agents"] = [
        {"agent_id": "agent_mate", "agent_name": "Mate",
         "agent_description": "OCR", "via_team": True},
        {"agent_id": "agent_stranger", "agent_name": "Stranger",
         "agent_description": "Unrelated", "via_team": False},
    ]

    text = "\n".join(module._volatile_context_parts(ctx))

    mate_line = next(ln for ln in text.splitlines() if "agent_mate" in ln)
    other_line = next(ln for ln in text.splitlines() if "agent_stranger" in ln)
    assert "teammate" in mate_line
    assert "teammate" not in other_line


def test_the_delivery_rule_does_not_claim_plain_text_reaches_nobody():
    """The loudest line in the block was also the one a team room contradicts.

    "Ending the turn with the result only as plain text delivers NOTHING" is
    true wherever the agent must call a delivery tool, and exactly backwards in
    a team room, whose turn prompt says the opposite in the same context
    window: the plain text IS the reply, and a delivery tool would double-post.
    """
    # Case-folded: a negative assertion that only catches the exact casing it
    # was written against is a guard the next edit walks straight through.
    text = _static_text().lower()

    assert "delivers nothing" not in text
    assert "only as plain text" not in text


def test_the_delivery_obligation_itself_survives():
    """Deleting the contradiction must not delete the P0 it was written for.

    2026-08-01: five agents did real research and ended their turns with the
    results as plain text on a surface that delivered none of it. The
    obligation stays; only the mechanism moves.
    """
    text = _static_text()

    assert "Finished work is never ping-pong" in text


def test_the_delivery_rule_defers_the_mechanism_to_the_surface():
    """Both ways of delivering have to be named, because both are real.

    The block cannot know which surface this turn is on, so it states the duty
    and points at the turn for the how — the same move the visibility rewrite
    above made.
    """
    line = next(
        ln for ln in _static_text().splitlines()
        if "Finished work is never ping-pong" in ln
    )

    assert "bus_send_message" in line
    assert "posted for you" in line


def test_the_delivery_rule_states_the_team_case_as_a_prohibition_and_first():
    """A two-branch rule whose branches BOTH match is not a rule.

    The first draft read "when a bus delivery tool is offered, send with it;
    when your reply is posted for you, writing it IS delivering". On a team
    turn both antecedents hold — the team gate empties the expressive
    declaration, but the tool schemas stay in context because this module has
    no `get_disallowed_tools` override — and the sentence gave no precedence.
    The branch a model can act on straight from the tool list is the one that
    double-posts.

    So the team case comes FIRST and is phrased as a prohibition, and the tool
    branch is the fallthrough.
    """
    line = next(
        ln for ln in _static_text().splitlines()
        if "Finished work is never ping-pong" in ln
    )

    assert "do NOT also call a delivery tool" in line
    assert line.index("posted for you") < line.index("bus_send_message")


def test_no_rule_flatly_calls_the_counterparty_a_machine():
    """The species claim had a second copy, with the behaviour attached.

    Retracting "the message came from another agent, NOT from your owner" while
    leaving "The other party is another agent, not a human. Skip pleasantries"
    fourteen lines below keeps the harm and drops only its justification — the
    imperative half re-asserts the claim on its own authority, and a team
    room's owner posts over the bus.
    """
    text = _static_text()

    assert "The other party is another agent, not a human." not in text
    # The brevity half carries the ping-pong P0 and must survive the scoping.
    assert "Brevity beats politeness" in text


def test_absence_of_the_tag_is_not_claimed_to_mean_the_owners_chat_window():
    """An IM turn says the opposite in the same context window.

    `channel/channel_prompts.py` tells a Lark/Slack/Telegram/Discord turn the
    message "arrived via {channel}, NOT from your owner's chat window" — and a
    job turn has no human at all. The absence of a bus tag says exactly one
    thing: it did not come over the bus.
    """
    line = next(
        ln for ln in _static_text().splitlines()
        if "When there is no `[MessageBus" in ln
    )

    assert "did not arrive over the bus" in line
    assert "is from your owner via the main chat interface" not in line


def test_the_worked_example_is_the_tag_the_code_actually_emits():
    """The example taught a four-field tag; both render sites build three.

    Harmless while the tag was decoration. Once the rules say "read the sender
    in the tag", an agent following the example hunts for a display name that
    is never there and finds the CHANNEL in the position it was told holds an
    id. Generated from the same helper so the two cannot drift again.
    """
    from xyz_agent_context.module.message_bus_module.message_bus_module import (
        _bus_tag,
    )

    text = _static_text()

    assert _bus_tag("agent_xxx", "ch_yyy") in text
    assert "[MessageBus · AgentName · agent_xxx · ch_yyy]" not in text


def test_the_input_source_tag_names_this_turns_channel_not_the_newest_dm():
    """`get_unread` is cross-channel; the tag must not be.

    A DM landing while a team turn is being assembled would otherwise name that
    peer as the sender of the message this turn is answering — and the rules
    now make the tag the authority on who is speaking.
    """
    import asyncio
    from types import SimpleNamespace

    from xyz_agent_context.schema.context_schema import ContextData
    from xyz_agent_context.module.message_bus_module import message_bus_module as mod

    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"

    room_msg = SimpleNamespace(
        message_id="m1", channel_id="ch_team", from_agent="usr_a1b2c3",
        content="where are we", model_dump=lambda: {
            "from_agent": "usr_a1b2c3", "channel_id": "ch_team",
            "content": "where are we",
        },
    )
    # Arrives LAST, in a different room — the old `[-1]` would have named it.
    dm_msg = SimpleNamespace(
        message_id="m2", channel_id="ch_dm", from_agent="agent_peer",
        content="unrelated", model_dump=lambda: {
            "from_agent": "agent_peer", "channel_id": "ch_dm",
            "content": "unrelated",
        },
    )

    fake_bus = SimpleNamespace(
        get_unread=_async_return([room_msg, dm_msg]),
        count_unread=_async_return(2),
        get_channel_members=_async_return([]),
        _db=SimpleNamespace(execute=_async_return([])),
    )

    ctx = ContextData(
        agent_id="agent_me", user_id="usr_1", input_content="hi",
        working_source=WorkingSource.MESSAGE_BUS,
    )
    ctx.extra_data["working_source"] = WorkingSource.MESSAGE_BUS
    ctx.extra_data["bus_channel_id"] = "ch_team"
    # The tagger reads the input out of extra_data (and no-ops on an empty
    # one), which is how the runtime hands it over.
    ctx.extra_data["input_content"] = "hi"

    monkey = mod._get_default_bus_async
    mod._get_default_bus_async = _async_return(fake_bus)
    try:
        asyncio.run(module.hook_data_gathering(ctx))
    finally:
        mod._get_default_bus_async = monkey

    tagged = ctx.extra_data["input_content"]
    assert tagged.startswith("[MessageBus · User · ch_team]")
    assert "agent_peer" not in tagged
    assert "ch_dm" not in tagged


def test_silence_is_producing_nothing_not_merely_calling_nothing():
    """"Just stop the turn" is a tool-call instruction on a text-delivery surface.

    Where the reply auto-posts, ending the turn with any leftover text still
    sends a message — so "do not call the tool" does not add up to silence and
    the rule has to say what silence actually costs.
    """
    line = next(
        ln for ln in _static_text().splitlines()
        if "choose silence explicitly" in ln
    )

    # No trailing period in the negative: re-adding "Just stop the turn" with
    # any other punctuation would have sailed past the first version of this.
    assert "just stop the turn" not in line.lower()
    # And silence is toward the BUS. This same block obliges an owner relay
    # forty lines down ("never suppresses reporting back to your owner"), so an
    # unqualified "no reply text at all" can swallow the answer a person is
    # waiting for.
    assert "no reply text to the bus conversation" in line


def test_the_bus_tag_is_not_claimed_to_mean_the_sender_is_a_machine():
    """A team room carries its owner's OWN messages over the bus.

    They reach the unread list — and the input tag — as `usr_<id>`, so a flat
    "this came from another agent, NOT from your owner" is false exactly where
    a person is waiting for an answer, and it arrives attached to a rule that
    says to drop the pleasantries.
    """
    text = _static_text()

    assert "NOT from your owner" not in text
    assert "a person can speak on the bus" in text


def test_a_person_on_the_bus_renders_as_one_in_the_unread_list():
    """The rule "read the sender" only works if the sender is readable.

    The rendering printed the raw `usr_a1b2c3` synthetic id, which tells a
    model nothing — least of all that it is looking at its owner.
    """
    from xyz_agent_context.schema.context_schema import ContextData

    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    ctx = ContextData(agent_id="agent_me", user_id="usr_1", input_content="hi")
    ctx.extra_data["bus_unread_messages"] = [
        {"from_agent": "usr_a1b2c3", "channel_id": "ch_1", "content": "where are we"},
        {"from_agent": "agent_peer", "channel_id": "ch_1", "content": "ping"},
    ]

    text = "\n".join(module._volatile_context_parts(ctx))

    human_line = next(ln for ln in text.splitlines() if "where are we" in ln)
    peer_line = next(ln for ln in text.splitlines() if "ping" in ln)
    assert "User" in human_line
    assert "usr_a1b2c3" not in human_line
    assert "agent_peer" in peer_line


def test_the_unread_header_does_not_repeat_the_retracted_promise():
    """The same sentence, a hundred lines down, sitting on the list it is
    wrong about.

    The static rules were scoped to DMs because a team room clears its cursor
    once a turn has rendered it. The volatile block still printed the
    unqualified version — directly above an unread list that MIXES team-room
    messages in. Fixing one copy and leaving the other is how a contradiction
    survives a PR that was written to remove it.
    """
    from xyz_agent_context.schema.context_schema import ContextData

    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    ctx = ContextData(agent_id="agent_me", user_id="usr_1", input_content="hi")
    ctx.extra_data["bus_unread_messages"] = [
        {"from_agent": "agent_peer", "channel_id": "ch_1", "content": "ping"}
    ]

    text = "\n".join(module._volatile_context_parts(ctx))

    assert "Ignored messages stay unread" not in text
    assert "Reply Discipline" in text


def test_the_team_prompt_really_does_promise_what_the_static_rule_defers_to():
    """The cross-file contract that makes the rewritten rule TRUE.

    The static block now says "if this turn's prompt tells you your reply is
    posted for you, writing it IS delivering it". That sentence is only correct
    while the team prompt actually makes that promise and actually forbids the
    delivery tools. Nothing pinned that half, so a reword on the trigger side
    could quietly turn the module's rule back into a lie — which is the exact
    failure mode this whole file exists to catch, one file over.
    """
    from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    prompt = trigger._build_team_prompt(
        "agent_me",
        [],
        [{"agent_id": "agent_me", "name": "Me", "description": "", "capabilities": []}],
        owner_user_id="usr_1",
        team_id="team_x",
        trigger_messages=[],
        bulletin=None,
    )

    # "posted for you" in the module's words; the room says it its own way.
    assert "posted to the group" in prompt
    # And the prohibition the module's rule leans on.
    assert "Do NOT deliver your answer through a function" in prompt
    assert "bus_send_message" in prompt
