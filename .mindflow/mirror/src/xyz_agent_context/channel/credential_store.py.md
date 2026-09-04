---
code_file: src/xyz_agent_context/channel/credential_store.py
last_verified: 2026-09-04
stub: false
---

# channel/credential_store.py — GenericCredentialStore

## Intent

The ONE credential store every IM channel is served from (table `channel_credentials`, one row per (channel, agent)). Values are split by the channel's `CredentialSchema`: declared secrets — and anything that looks like one (token/secret/password/key) unless declared public — go encrypted into `secret_json`, identity fields into `public_json`, the schema's `external_id_field` into the channel-wide unique `external_id`. Descriptors come from `ingress.channels` (an unknown channel is `UnknownChannel`). Plugin channels write here directly (generic routes); builtin channels are mirrored in during the dual-write phase (`credential_mirror`) and switch their reads here in 4d.
