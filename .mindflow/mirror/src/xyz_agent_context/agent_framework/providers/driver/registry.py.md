---
code_file: src/xyz_agent_context/agent_framework/providers/driver/registry.py
last_verified: 2026-09-03
stub: false
---

# registry.py — driver_type → class map

Module-level ``dict`` populated by the ``@register`` decorator at import
time. The resolver consults it via ``get_driver_class(driver_type)``;
unknown keys return ``None`` and the resolver raises
``LLMConfigNotConfigured`` — that's intentionally loud so a misconfigured
row never silently routes to a default that bills the wrong account.

Re-registering the same class is a no-op (idempotent). Re-registering
a different class under the same key logs a warning and overwrites —
this only triggers under test fixtures that monkeypatch drivers; in
production every driver registers exactly once.

``SystemDriver`` doesn't use the decorator directly; instead its module
calls ``register(SystemDriver)`` inside an ``if is_cloud_mode():``
block. Local installs never see it in the registry, which means a
``driver_type='system_pool'`` row on a local DB raises the loud error
above instead of half-working.

## 2026-09-03 — `DRIVER_REGISTRY` 变成内核 `Registry[Type]`（`model.providers` 位）

`@register` 语义逐字保留（同类 no-op、异类覆盖并 warning），底层从 dict 换成
`narranexus.kernel.plugins.registry.Registry`，工厂返回类本身。`get_driver_class` 走 `try_get`。
`"x" in DRIVER_REGISTRY` 仍成立（Registry 实现 `__contains__`），但 `DRIVER_REGISTRY["x"]` 不再
支持——唯一的调用方 `tests/agent_framework/test_codex_oauth_driver.py` 已改用 `get_driver_class`
（rule 2 不留兼容垫片）。`register` 多了 keyword-only `owner`（默认 `builtin.providers`）。
