"""ChatModule package surface.

Re-exports the shared, dialect-safe read helper from ``_chat_reads`` so the
AgentDataStore seam's DirectStore, the agent-scoped backend twin route, and tests
import the PACKAGE rather than the private leaf. The ChatModule class is imported
from ``.chat_module`` by MODULE_MAP and is deliberately not re-exported here (same
choice job_module / awareness_module make — avoids an init-order coupling)."""
from ._chat_reads import fetch_chat_history

__all__ = ["fetch_chat_history"]
