"""
@file_name: test_mcp_identity_team.py
@author: NarraNexus
@date: 2026-08-07
@description: The turn's team rides the server-side identity channel.

Why it must be server-side: `register_artifact` takes `agent_id` as a tool
PARAMETER the model fills in, so nothing the tool receives from the model can
be trusted to say "this is a team turn" — a model in a private chat could
claim otherwise and write into a team's workspace. The identity headers exist
precisely because a machine-knowable fact must not depend on model obedience
(see module/_mcp_identity.py), so the team rides there too.

`errand_channel` deliberately is NOT reused for this: it is populated only
when the turn continues the agent's own errand (message_bus_trigger), so it
is empty for most team turns and would read as "not a team turn".

The bearer contract says a new fact is APPENDED and added to BEARER_FIELDS —
these tests pin that it was appended (never inserted), because inserting
mid-record silently reassigns the meaning of every later position.
"""

from __future__ import annotations

from xyz_agent_context.module._mcp_identity import (
    BEARER_FIELDS,
    TEAM_ID_HEADER,
    _parse_bearer,
    agent_id_headers,
)


def test_team_id_is_appended_not_inserted():
    """Order is frozen; a new field goes on the end. Inserting one would make
    every older reader parse the following fields as the wrong facts.

    `team_id` sits at #7, not #6, because `root_run_id` was written in parallel
    (the cascade-stop work) and reached dev first — so #6 is already on the
    wire. Taking it would make an in-flight bearer decode a team id as a run
    id, and the cascade stop selects an entire trigger tree by that value.
    Whoever lands second moves; that is the whole rule.
    """
    assert BEARER_FIELDS[:5] == (
        "agent_id", "turn_source", "errand_peer", "errand_channel", "user_id"
    )
    assert BEARER_FIELDS[5] == "root_run_id"
    assert BEARER_FIELDS[6] == "team_id"


def test_headers_carry_team_on_both_channels():
    """codex can only forward the bearer, claude forwards explicit headers —
    a fact shipped on one channel only makes one adapter a degraded consumer."""
    headers = agent_id_headers("agent_a", user_id="user_1", team_id="team_9")

    assert headers[TEAM_ID_HEADER] == "team_9"
    assert _parse_bearer(headers["Authorization"]).team_id == "team_9"


def test_team_survives_empty_middle_fields():
    """Most team turns have no errand scope, so the fields before team_id are
    blank. Those must stay as empty placeholders or team_id shifts position."""
    headers = agent_id_headers("agent_a", team_id="team_9")

    parsed = _parse_bearer(headers["Authorization"])
    assert parsed.agent_id == "agent_a"
    assert parsed.team_id == "team_9"
    assert parsed.errand_channel is None


def test_no_team_emits_no_team_header():
    """A private turn must be indistinguishable from before this change."""
    headers = agent_id_headers("agent_a", user_id="user_1")

    assert TEAM_ID_HEADER not in headers
    assert _parse_bearer(headers["Authorization"]).team_id is None


def test_old_bearer_without_team_still_parses():
    """Rows/records minted before this field exists must keep working —
    readers tolerate any field count (fail-open, never a hard error)."""
    parsed = _parse_bearer("Bearer nx-agent:agent_a~message_bus~~~user_1")

    assert parsed.agent_id == "agent_a"
    assert parsed.user_id == "user_1"
    assert parsed.team_id is None
