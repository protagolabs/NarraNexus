---
code_file: frontend/src/lib/frameworkBrand.ts
last_verified: 2026-09-04
stub: false
---

# frameworkBrand.ts — framework id → UI 标签与品牌图标的唯一出处

## 为什么存在

Dashboard 目录表与 Agent 档案页各自拷了一份 `formatFramework` + 图标映射，PR #383 内就漂了
一次（一边认 `nexus_power` 一边不认，tsc 不响）。评审二轮 I2 指出这正是同 PR 两个 🔴 修的
「同一事实两份实现」形状。与 [[modelBrandIcons.ts]]、DashboardPage 的 `CHANNEL_BRANDS`
同一层。

## 两条刻意的行为

- **未知 id** 从原串 title-case，不映射到别的品牌——「不认识但存在」好于「错」。
- **缺失 id**（undefined）渲染 `—` + 通用 Bot 图标。后端对不属于观看者的 agent 留空；
  这里若补默认值就会显示一个它可能没在跑的品牌。

后端 `model_identity.FRAMEWORK_DISPLAY_NAMES` 是 agent 在 prompt 里的自称，属于 prompt
文案，不要求与这里一致。

## 消费方

[[../pages/DashboardPage.tsx]]、[[../pages/AgentProfilePage.tsx]]。
