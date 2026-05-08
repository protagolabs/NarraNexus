"""
@file_name: id_field_map.py
@author: NetMind.AI
@date: 2026-05-08
@description: Structured field registry for ID rewrite (PRD §8.11 Layer 2)

Each table → {column_or_jsonpath: id_kind}.
- "col_name"            ← direct column substitution
- "col_name[*]"         ← JSON list of IDs; substitute each element
- "col_name.subkey"     ← JSON object's subkey
- "col_name[*].subkey"  ← list of objects, substitute each element's subkey
"""

import secrets
from typing import Dict


STRUCTURED_ID_FIELDS: Dict[str, Dict[str, str]] = {
    "agents": {"agent_id": "agent"},
    "events": {
        "event_id": "event",
        "agent_id": "agent",
        "narrative_id": "narrative",
    },
    "narratives": {
        "narrative_id": "narrative",
        "agent_id": "agent",
        "main_chat_instance_id": "instance",
    },
    "agent_messages": {
        "message_id": "message",
        "agent_id": "agent",
        "narrative_id": "narrative",
        "event_id": "event",
    },
    "module_instances": {
        "instance_id": "instance",
        "agent_id": "agent",
    },
    "instance_social_entities": {
        "instance_id": "instance",
    },
    "instance_jobs": {
        "instance_id": "instance",
        "job_id": "job",
        "agent_id": "agent",
    },
    "instance_rag_store": {
        "instance_id": "instance",
    },
    "instance_narrative_links": {
        "instance_id": "instance",
        "narrative_id": "narrative",
    },
    "instance_awareness": {
        "instance_id": "instance",
    },
    "instance_module_report_memory": {
        "instance_id": "instance",
    },
    "instance_json_format_memory": {
        "instance_id": "instance",
    },
    "instance_json_format_memory_chat": {
        "instance_id": "instance",
    },
    "module_report_memory": {
        "instance_id": "instance",
    },
    "bus_channels": {
        "channel_id": "channel",
    },
    "bus_channel_members": {
        "channel_id": "channel",
        "agent_id": "agent",
    },
    "bus_messages": {
        "message_id": "message",
        "channel_id": "channel",
    },
    "bus_agent_registry": {
        "agent_id": "agent",
    },
    "teams": {
        "team_id": "team",
    },
    "team_members": {
        "team_id": "team",
        "agent_id": "agent",
    },
}


# kind → prefix (for new ID generation).
ID_KIND_PREFIXES: Dict[str, str] = {
    "agent": "agent",
    "event": "evt",
    "narrative": "nar",
    "instance": "inst",
    "message": "msg",
    "job": "job",
    "team": "team",
    "channel": "ch",
    "mcp": "mcp",
}


def gen_new_id(kind: str) -> str:
    prefix = ID_KIND_PREFIXES.get(kind)
    if not prefix:
        raise ValueError(f"Unknown ID kind: {kind}")
    return f"{prefix}_{secrets.token_hex(6)}"
