---
code_file: tests/agent_framework/test_self_heal_onboard_default.py
last_verified: 2026-10-16
stub: false
---

# test_self_heal_onboard_default.py — self-heal 修复目标守卫

锁定 `pick_default_model` / `self_heal_if_broken` 的新修复顺序(onboarding 默认 → 目录默认 → 卡表头 → 目录表头)。
核心用例是线上真实形态:NetMind 卡表头为 omni 模型、列表含 V4.1-Flash 但不含已消失的 V4-Flash,槽位修复必须落到 V4.1-Flash
(agent 与 helper_llm 各一),而不是表头。另含无槽位种类、无 onboarding 条目来源(普通 BYOK)的不变行为。
把 `pick_default_model` 改回"取卡表头"这些测试必须变红。
