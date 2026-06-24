---
code_file: src/xyz_agent_context/narrative/_narrative_impl/team_room.py
last_verified: 2026-06-24
stub: false
---

# team_room.py — dedicated narrative for a team group-chat room

## Why it exists

Team group chat runs over the message bus (see [[message_bus_trigger.py]] /
`teams.py`). The root-cause bug it fixes: [[agent_runtime.py]] overrides a run's
`user_id` to the agent **owner** for every trigger (so all triggers share one
workspace). For a team-chat run that meant the reply executed under the owner's
real id, so 1:1 narrative selection picked an owner narrative, the ChatModule
instance was created under the owner, and the group chat leaked into the owner's
1:1 chat history / sidebar / BM25 routing — events were indistinguishable from
real 1:1 (`trigger_source` = the user).

The cure is **identity isolation**: each `(agent, channel)` gets ONE stable
narrative (`is_special="team_room"`) whose user actor — and whose ChatModule
instance (provisioned lazily by [[step_1_select_narrative.py]]) — is a
room-scoped pseudo-user `room_<channel_id>`, never the owner. Every owner-keyed
1:1 read surface filters by the owner id and therefore never sees it. No changes
to the 1:1 read routes were needed; isolation is purely by key.

## Upstream / Downstream

- `build_team_room_narrative_id(agent, channel)` — deterministic
  `nar_room_<sha1(agent:channel)[:24]>`. Per-agent (narratives are per-agent) and
  stable so every turn routes to the same room memory; fits VARCHAR(128).
- `build_team_room_user_id(channel)` → `room_<channel_id>` — the isolating key.
- `create_team_room_narrative(...)` — builds (does NOT persist) the `Narrative`,
  mirroring `default_narratives.create_default_narrative`. Persisted +
  get-or-created by `NarrativeService.get_or_create_team_room_narrative`
  (load_by_id → upsert, concurrency-safe). The bus trigger calls that and passes
  the id as `forced_narrative_id`.

## Gotchas

- The ChatModule instance is intentionally NOT created here — `step_1`'s
  `_ensure_user_chat_instance` provisions it under `room_user_id` (resolved via
  `_chat_user_id_for_narrative`) on first use, same lazy pattern as defaults.
- `env_variables` carries `bus_channel_id` + `room_user_id`; `step_1` reads
  `room_user_id` from there, so the narrative layer stays free of bus coupling.
- Memory indexing is left ON (Owner decision 2026-06-24): the room narrative is
  projected into the agent-scoped search index, so group chat IS part of the
  agent's unified `remember`/Narra-Memory recall. This does not affect 1:1
  narrative ROUTING, which filters by owner actor and never matches the room.
