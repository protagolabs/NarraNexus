---
code_file: packages/narranexus-contracts/src/narranexus/contracts/channel.py
last_verified: 2026-09-07
stub: false
---

# contracts/channel.py — ChannelDescriptor (slot `ingress.channels`)

## Intent

A channel was six scattered facts (trigger map row, module_registry entry, data-access `CHANNELS` row, `WorkingSource` member, message-source handler, frontend row). `ChannelDescriptor` is that record as data: name (= the inbound WorkingSource value), transport (`socket` / `poll` / `webhook` / `none`), `CredentialSchema` (fields split into public identity and secrets; `external_id_field` is the channel-wide unique bot/app id), lazy `pkg.mod:Class` references for the trigger / module / credential manager / bind-test service, and `ChannelUi`. Every platform table that used to name channels is now a view over the registry; a channel plugin is one descriptor plus its classes. Batch 4b builds the generic credential store and routes on `credential_schema`; 4c the webhook transport on `transport`.

## 2026-09-04 · `bind_fields` (batch 4d.3)

`ChannelDescriptor.bind_fields` declares what a bind CALL takes when it differs from the stored credential shape — a manager-backed channel's service binds from e.g. a pasted link (NarraMessenger) or resolves an owner e-mail (Slack/Lark) and stores something else. Empty means the stored schema is the bind input (plugin channels). The generic route validates bodies against it (`credential_store.bind_fields_for` / `validate_bind_fields`) and exposes it in the schema view so a UI can render the bind form for any channel.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.

## 2026-09-07 — channel name is ASCII [a-z0-9_]

__post_init__ validates with a regex: str.isalnum() is Unicode-aware, so a name like 'café' passed a check whose message promised [a-z0-9_] and then became an invalid SQL alias / URL segment.
