---
code_file: src/xyz_agent_context/channel/credential_store.py
last_verified: 2026-09-04
stub: false
---

# channel/credential_store.py — GenericCredentialStore

## Intent

The ONE credential store every IM channel is served from (table `channel_credentials`, one row per (channel, agent)). Values are split by the channel's `CredentialSchema`: declared secrets — and anything that looks like one (token/secret/password/key) unless declared public — go encrypted into `secret_json`, identity fields into `public_json`, the schema's `external_id_field` into the channel-wide unique `external_id`. Descriptors come from `ingress.channels` (an unknown channel is `UnknownChannel`). Plugin channels write here directly (generic routes); builtin channels are mirrored in during the dual-write phase (`credential_mirror`) and switch their reads here in 4d.

## 2026-09-04 · webhook transport (batch 4c)

`CredentialRecord.app_id` (external id, else agent id) — the subscriber key `ChannelTriggerBase` expects on a credential, so generic-store records drive a trigger directly.

## 2026-09-04 · generic store as the source of truth (batch 4d.1)

`patch(channel, agent_id, fields, expect=)` is an optimistic field-level merge: the row carries `version`, a write only lands when the version it read is still current, else re-read and retry — so two writers on DISJOINT fields (a bind panel and a trigger's status update) never clobber each other; `expect` makes it a compare-and-set (`update_if`). `find_one(channel, external_id=…, **public)` answers the channel-wide 'is this bot already bound?' question. `upsert` bumps the version and stamps `created_at` on insert.
