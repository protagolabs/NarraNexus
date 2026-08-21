"""
@file_name: steer_schema.py
@author: Bin Liang
@date: 2026-08-21
@description: Live-steering injection — one message routed INTO a turn
that is already running, instead of triggering a fresh turn.

One uniform inbox for the "append to the running loop" capability. Its
job is DECOUPLING, not durability: the running loop drains ONE store
instead of reaching into each producer's native home. That matters
because the producers are heterogeneous — a team room post already lives
in ``bus_messages``, but an owner-chat interjection is not a bus message
at all (it runs the chat memory path), so "just query bus_messages"
would miss half the producers, and every new source would add another
special case in the feeder. Same outbox/inbox pattern the codebase
already uses for ``instance_artifact_events``. (IM producers are out of
scope for v1.) A producer writes one of these keyed by the target run;
the transport drains the run's unconsumed rows into its ``SteeringInlet``
at the next step boundary.

``consumed_at`` is the OTHER thing the bus cannot lend: a per-RUN consume
cursor. The bus's ``last_processed_at`` is per ``(agent, channel)`` and
is already the trigger's (it decides new dispatches); it cannot double as
a running turn's steer cursor — least of all when one agent has several
concurrent runs. Per row, per run, is the only shape that survives that.

``run_id`` is deliberately OPAQUE here: this schema does not know how the
orchestrator identifies a live run (that is the RunRegistry's concern),
only that whoever produces and whoever drains agree on the handle. That
keeps the storage layer decoupled from the routing design.

``source`` records which producer wrote it so the prompt layer can phrase
the injection appropriately (a teammate's room message reads differently
from the owner interjecting) — the mechanism is identical, the wording is
not. Injection is append-only by contract; nothing here mutates a prior
row's content.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class SteerSource:
    """Which producer wrote an injection. Not the model's to set."""

    TEAM = "team"          # a message posted to the run's team room
    OWNER_CHAT = "owner_chat"  # the owner interjecting in a 1:1 chat


class SteerInjection(BaseModel):
    """One message to fold into a running turn's context.

    ``id`` is the arrival order and the consume cursor's unit; it is
    assigned by the store, so it is ``None`` until persisted.
    ``consumed_at`` is ``None`` while the row is still pending; it is
    stamped when the run has drained the row, which is what keeps a
    message from being injected twice.
    """

    id: Optional[int] = None
    run_id: str
    msg_id: str
    role: str = "user"
    content: str
    sender_id: str
    source: str
    created_at: Optional[datetime] = None
    consumed_at: Optional[datetime] = None
