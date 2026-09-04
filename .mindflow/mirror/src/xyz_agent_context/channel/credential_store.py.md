---
code_file: src/xyz_agent_context/channel/credential_store.py
last_verified: 2026-09-04
stub: false
---

# channel/credential_store.py — GenericCredentialStore

## Intent

The ONE credential store every IM channel is served from (table `channel_credentials`, one row per (channel, agent)). Values are split by the channel's `CredentialSchema`: declared secrets — and anything that looks like one (token/secret/password/key) unless declared public — go encrypted into `secret_json`, identity fields into `public_json`, the schema's `external_id_field` into the channel-wide unique `external_id`. Descriptors come from `ingress.channels` (an unknown channel is `UnknownChannel`). Plugin channels write here through the generic routes; since batch 4d the six builtin managers persist here too (the retired per-channel tables are copied in once by [[credential_legacy]]).

## 2026-09-04 · webhook transport (batch 4c)

`CredentialRecord.app_id` (external id, else agent id) — the subscriber key `ChannelTriggerBase` expects on a credential, so generic-store records drive a trigger directly.

## 2026-09-04 · generic store as the source of truth (batch 4d.1)

`patch(channel, agent_id, fields, expect=)` is an optimistic field-level merge: the row carries `version`, a write only lands when the version it read is still current, else re-read and retry — so two writers on DISJOINT fields (a bind panel and a trigger's status update) never clobber each other; `expect` makes it a compare-and-set (`update_if`). `find_one(channel, external_id=…, **public)` answers the channel-wide 'is this bot already bound?' question. `upsert` bumps the version and stamps `created_at` on insert.

## 2026-09-04 · every builtin channel reads here; `list_for_agent` (batch 4d.2)

NarraMessenger and Lark joined the four managers switched in 4d.1, and `credential_mirror` is deleted — there is no second copy of any binding any more. `list_for_agent(agent_id)` returns an agent's bindings across channels (bundle export, agent deletion); records decode secrets for the caller, so the caller decides what leaves the process (the bundle exporter ships them only under the explicit opt-in).

## 2026-09-04 · bind-input validation (batch 4d.3)

`bind_fields_for(descriptor)` (the descriptor's `bind_fields`, else the stored schema) and `validate_bind_fields(descriptor, values)` — unknown names rejected (fail-closed: they would reach a service's `do_bind(**fields)` as a TypeError), required non-blank, select within options — are what the generic bind route checks before any service runs.
