"""
@file_name: run_registry.py
@author: Bin Liang
@date: 2026-08-21
@description: RunRegistry — the per-process index of live runs by
(agent_id, surface), the routing brain for live steering.

A producer that has a new message for an agent asks "is this agent already
running on THIS surface?". If yes, the message is steered into that run
(appended to its context at the next step boundary) instead of dispatching
a fresh turn. Surface-scoping is the whole point: one agent may have several
concurrent runs — multiple team rooms, a web chat, a job — and a message for
one surface must NEVER route into a run on a different surface (that would
splice a team-A message into a team-B turn's context).

Why per-process, in-memory, not a DB table: a run's steer handle (the live
push into its loop) only exists in the process that owns the run, and for the
v1 producers the producer is co-located with its target run — a team message
and the team run are both in the bus-trigger process; an owner-chat
interjection and the web run are both in the backend process. So the process
that produces is the process that holds the run. This is a seam (iron rule
#20, like ``get_admission_controller``): the interface stays the same if a
future cross-process producer needs the truth moved behind Redis / a DB view.

``run_id`` is opaque here and to ``steer_inbox``: whoever registers a run
mints the handle (it need not be the late-bound ``events.event_id``), and the
registry never interprets it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class RunHandle:
    """One live run's routing entry. ``steer`` is the opaque push handle the
    owner registered (e.g. a channel that writes into the loop's inlet); the
    registry stores and returns it without interpreting it."""

    run_id: str
    agent_id: str
    surface_key: str
    steer: Any


class RunRegistry:
    """In-memory, single-event-loop. Methods are synchronous dict ops with no
    await between read and write, so they are atomic on one event loop; the
    seam can move behind a lock / Redis if a thread or replica ever shares it.
    """

    def __init__(self) -> None:
        self._by_run: Dict[str, RunHandle] = {}
        self._by_surface: Dict[Tuple[str, str], str] = {}

    def register(self, agent_id: str, surface_key: str, run_id: str, steer: Any) -> None:
        """Record a live run. A surface holds at most one live run — a repeat
        register on the same ``(agent, surface)`` supersedes the older one
        (its ``run_id`` mapping is overwritten; a late ``release`` of the
        superseded run then finds the surface no longer points at it)."""
        handle = RunHandle(run_id=run_id, agent_id=agent_id, surface_key=surface_key, steer=steer)
        self._by_run[run_id] = handle
        self._by_surface[(agent_id, surface_key)] = run_id

    def live_run(self, agent_id: str, surface_key: str) -> Optional[RunHandle]:
        """The live run for ``(agent, surface)``, or None. A match on a
        different surface or a different agent is impossible by construction —
        the key is the pair."""
        run_id = self._by_surface.get((agent_id, surface_key))
        if run_id is None:
            return None
        return self._by_run.get(run_id)

    def release(self, run_id: str) -> None:
        """Drop a finished run. Only clears the surface mapping if it still
        points at THIS run — so a late release of a superseded run never
        evicts the run that replaced it on the same surface."""
        handle = self._by_run.pop(run_id, None)
        if handle is None:
            return
        key = (handle.agent_id, handle.surface_key)
        if self._by_surface.get(key) == run_id:
            del self._by_surface[key]


_registry: Optional[RunRegistry] = None


def get_run_registry() -> RunRegistry:
    """The process-singleton registry (the seam, like ``get_admission_controller``).
    One per process naturally partitions by owner: the bus-trigger process holds
    its bus runs, the backend process holds its web runs."""
    global _registry
    if _registry is None:
        _registry = RunRegistry()
    return _registry


__all__ = ["RunRegistry", "RunHandle", "get_run_registry"]
