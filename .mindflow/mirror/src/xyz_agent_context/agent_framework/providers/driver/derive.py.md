---
code_file: src/xyz_agent_context/agent_framework/providers/driver/derive.py
last_verified: 2026-08-28
stub: false
---

## 2026-08-27(review 第 5 轮)— derive_auth_ref 签名改 (source, auth_type) 无默认值

source 守卫落地后,省略 source 只会返回 None——这个默认值不存在正确
用法,且参数序与兄弟函数(`derive_driver_type(source, auth_type,
protocol)` / `derive_billing_policy(source, auth_type)`)相反,按位置
并排调用时写反不报错、只产出空 auth_ref(P1 零信号复现)。现签名
`(source, auth_type)`、无默认,漏传即 TypeError。**None 与 "" 的区分
必须保留**:`from_row` 的 `or derive(...)`、`_cli_subscription_row_fields`
的 `or ""`、`build_codex_config` 的 `or (card.auth_ref or "")` 三处都
依赖 None 触发回落。真值表有直接的表驱动测试钉住
(`test_derive_auth_ref_truth_table`,断言 `is None` 非 falsy)。
2026-08-28(PR bot 轮):CLI 订阅源集合抽成模块常量
`CLI_SUBSCRIPTION_SOURCES`(进 `__all__`),derive_auth_ref 的守卫与
user_service `_cli_subscription_row_fields` 的 scope 守卫共用;加第三个
CLI framework 时集合与 per-source 分支都要扩,helper 的非 None 兜底会把
半成品扩展变成响亮的 ValueError 而非静默 driver_type=NULL 行。

## 2026-08-27 — derive_auth_ref 的 source 守卫收进函数本体(P1 review 第 2 轮)

旧真值表对任意 `auth=="oauth"` 的行默认发 **claude** 哨兵,靠调用方
(backfill 的 `if driver_type in {...}`)兜边界;`ProviderCard.from_row`
的读时推导落地后成了第二个调用方且没抄那道守卫——手工构造
`card_type=anthropic + auth_type="oauth"`(路由层 auth_type 是自由字符串)
的行会继承 claude 哨兵,**用宿主订阅凭证给一张自身无凭证的卡验绿**。
现在守卫在函数内:source 不在 {claude_oauth, codex_oauth} 一律 None,
全部调用方自动同口径,backfill 的外置守卫同 commit 删除。消费者清点
(review 第 4 轮补齐):插入时(user_service
`_cli_subscription_row_fields`)、读时(`ProviderCard.from_row`)、
backfill、codex helper 构造(cli_helper)、`build_codex_config` 强制
覆盖(后两者 source 传字面量 "codex_oauth",语境即 codex)。注意:
oauth_token / 非 oauth 仍返回 **None 而非 ""**——from_row 的
`row.get(...) or derive(...)` 与 user_service `_cli_subscription_row_fields`
的 `or ""` 都依赖这个区分。测试:
`test_from_row_does_not_derive_auth_ref_for_non_cli_sources`。

## 2026-07-26 — `derive_billing_policy`：`oauth_token` → `external_oauth`

setup-token 与 host-CLI oauth 同为订阅运输层，token 由 Anthropic 侧计费
——只记 cost_records，不扣 quota。注意 `derive_auth_ref` 对 oauth_token
返回 None 是有意的：token 行没有凭据文件 sentinel（token 本身就是凭据，
在 api_key 列），backfill 的 `auth_ref is not None` 守卫因此不会碰
token 行。

# derive.py — pure helpers

## Why these functions live outside Driver classes

* ``backfill`` runs them against raw DB rows that aren't yet
  classified — Driver instances don't exist there.
* ``self_heal`` needs the "is this slot broken / what's a safe
  default" decision before it has decided which Driver to use.
* Tests can table-drive them without DB fixtures.

Keeping them in a separate module enforces that the logic is pure —
no DB calls, no side effects, no provider lookups.

## derive_driver_type

The truth table sits in the docstring. Two gotchas:

* ``user`` source maps to ``custom_anthropic`` / ``custom_openai`` —
  the legacy ``source='user'`` ProviderSource enum is intentionally
  ambiguous about protocol, so we disambiguate here.
* ``system`` source maps to ``system_pool`` — the corresponding
  Driver is cloud-only. Backfill on a local DB will never see a
  ``system`` source row because there's no UI to create one.

## derive_billing_policy

Three values: ``user_pays`` (default), ``system_quota`` (cloud
system pool), ``external_oauth`` (Claude OAuth — Anthropic does the
billing on their side). ``cost_tracker`` reads ``billing_policy``
post-call to decide whether to deduct from ``user_quotas``.

## derive_auth_ref + credential path resolvers

OAuth rows store a sentinel string ``claude-cli:~/.claude/.credentials.json``
or ``codex-cli:~/.codex/auth.json`` in ``auth_ref`` depending on
provider source. ``resolve_claude_credentials_path`` and
``resolve_codex_credentials_path`` expand those sentinels at use-time,
respecting the relevant override env vars so admins can relocate the
credentials file (or tests can inject a fake one).

## is_slot_broken + pick_default_model

The key business rule: the check is against the **card's own**
``models`` array, not against the global catalog. A user who configured
a private model that we don't recognise is fine; only a slot whose
model isn't in its own provider's list is broken.

``pick_default_model`` prefers the first element of card.models, falls
back to ``model_catalog.get_default_models(source, protocol)[0]``,
returns ``None`` if both are empty (caller logs and lets the call fail).
