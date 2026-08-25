---
code_file: tests/social_network_module/test_memory_write_audit_wiring.py
stub: false
last_verified: 2026-08-25
---
# test_memory_write_audit_wiring.py — 调用方那半的审计接线

[[_entity_updater.py]] 自己那八个 handler 由
[[test_entity_updater_alerts.py]] 覆盖。但**记忆写入并不只在那个文件里**：
[[social_network_module.py]] 自己创建主实体和每个被提及的第三方实体，这些
失败此前只有日志——而且因为 hook 在外层全吞，`agent_runtime` 那层告警永远
看不到它们。

那两个上报调用点上线时**一条测试都没有**，与本 PR 上一轮被打回的
「新胶水零覆盖」是同一个缺口，只是往上挪了一层楼。这个文件补的就是它。

## patch 目标必须是 `social_network_module`，不是 `_entity_updater`

[[social_network_module.py]] 在**模块顶层** import `_report_write_failure`，
函数体里持有的是**绑定后的引用**。所以隔壁测试文件那句
`monkeypatch.setattr(eu, "_report_write_failure", ...)` 对这两处**完全无效**
——断言会对着一个空列表静静通过。

这个坑不写下来，下一个加测试的人会先撞一次「patch 了但 calls 是空的」。

## 钉住了什么

- 两个 `operation` 的拼写（运维几周后 grep 的就是它）
- `entity_id` 必须是**失败的那一个**——`entity_id_candidate` 曾经是 `try` 内
  第一行而 `except` 里要读它，第一轮迭代会从 except 里抛 `NameError`（连带
  丢掉本批剩余实体），后续迭代会把失败记到**上一个**实体头上
- 一个实体失败不能中断整批，且后续实体照样审计
