"""Agent profile — the platform's own writer for an agent's name and description.

A domain package rather than a Module: the transaction writes the ``agents``
row and ``bus_agent_registry`` and is depended on by core HTTP routes, so
living inside a hot-pluggable Module made that Module un-unpluggable (铁律 #3).
The Awareness step it still needs is reached through a deferred, guarded import.
"""
from .agent_profile_service import AgentProfileWrite, apply_agent_profile_change

__all__ = ["AgentProfileWrite", "apply_agent_profile_change"]
