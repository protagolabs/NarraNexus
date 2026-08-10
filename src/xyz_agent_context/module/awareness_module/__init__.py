"""AwarenessModule package surface.

Re-exports the shared, dialect-safe write helper (and the identity-note string
helpers it is built from) from [[_awareness_writes]] so the AgentDataStore seam's
DirectStore, the agent-scoped backend twin route, and tests import the PACKAGE
rather than reaching the private leaf. The AwarenessModule class itself is
imported from ``.awareness_module`` by MODULE_MAP; it is deliberately NOT
re-exported here to keep this package __init__ free of the heavy module-load
chain (it must import cleanly for just the write helper)."""
from ._awareness_writes import (
    IDENTITY_CHANGE_SECTION,
    MAX_IDENTITY_CHANGE_ENTRIES,
    build_identity_change_note,
    merge_identity_change_note,
    update_agent_profile_from_args,
)

__all__ = [
    "IDENTITY_CHANGE_SECTION",
    "MAX_IDENTITY_CHANGE_ENTRIES",
    "build_identity_change_note",
    "merge_identity_change_note",
    "update_agent_profile_from_args",
]
