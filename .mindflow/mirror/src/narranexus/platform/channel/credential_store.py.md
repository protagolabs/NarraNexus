---
code_file: src/narranexus/platform/channel/credential_store.py
last_verified: 2026-09-09
stub: false
---

## 2026-09-09 — `set_enabled(..., reason=)` 写通用 `disabled_reason`（B-28）

`disabled_reason` 是频道无关的"平台把凭据关掉了，原因是什么"记录：`set_enabled` 现在走版本化的
`patch`（不再裸 update `enabled`），禁用时把 reason 写进 public 值，启用时清成 ""——generic
`set-active` 路由的用户重新启用自然清掉。trigger 基类的永久失败路径是第一个写入方。

## 2026-09-07 — 删掉 `descriptor_for` / `all_descriptors` 里的死 import

两处 `import narranexus.platform.module_system  # noqa: F401 — registers the builtin
descriptors (idempotent)` 已删。`register_all` 早就不存在了，这个 import 什么也不注册，
注释描述的是一份代码已经没有的契约。

行为**没有**变化，这一点由 `tests/channel/test_unbooted_process_fails_loud.py` 钉住：没
boot 的进程里 `descriptor_for` 照旧抛 `UnknownChannel`（响亮，不是「悄悄当作存在」），
`all_descriptors` 照旧返回空元组——空是诚实的答案（这个进程不知道任何渠道），配合前一条
就不会被误读成「而且查询还能用」。

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

## 2026-09-04 · `all_descriptors` (batch 4e)

`all_descriptors(registries=None)` lists every registered channel descriptor — the one registry read the instance factory, contact utils, dashboard and manyfold export use instead of naming channels.

## 2026-09-07 — per-row decrypt guard: secret_error / readable

SecretBox.decrypt fails closed (raises) on a Fernet token this key cannot open. Raising out of list_active turned one bad row — often another user's — into a whole-channel outage (the watcher retried forever). _row_to_record now catches the decrypt failure per row and returns the record with secret={} and secret_error='… re-bind required'; list_active skips such records (one warning per (channel, agent, version)), get()/find_one()/list_all() return them with the flag so callers can surface 'credential unreadable'. Fail-closed is preserved: an unreadable record never reaches a transport as an empty-but-valid credential.

## 2026-09-07 — CredentialConflict

upsert translates the DB unique-index violation ((channel, agent_id) / (channel, external_id)) into CredentialConflict(channel, external_id, agent_id); the DB stays the enforcement point (no application pre-check — that is the race), the route maps it to 409.

## 2026-09-07 — validate_bind_fields checks formats by field kind

email / url / int kinds are format-checked for every channel through the descriptor (the Lark bind used to check '@' in owner_email by hand and lost it in the generic migration).
