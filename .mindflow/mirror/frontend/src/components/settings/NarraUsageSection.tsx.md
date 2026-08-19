---
code_file: frontend/src/components/settings/NarraUsageSection.tsx
last_verified: 2026-08-19
stub: false
---

# NarraUsageSection.tsx — 「这里面有多少是 NarraNexus 花的」

## 为什么存在

2026-08-19 用户反馈（P2 · 用户体验）：**「usage 显示的是 model usage / api usage，
而不是 narra usage。这会导致我在别的平台使用 netmind model，但在 narra 上看到的余额
会减少。」**

反馈是对的，而且**不是 bug 是设计**：[[NetmindAccountPanel.tsx]] 上方的余额与流水
全部来自 NetMind 的 finance 域（[[netmind_billing_client.py]] 的
`/v1/finance/user-fee-info` 与 `/v1/finance/records`），口径是**整个 NetMind 账号**
—— 那个账号在任何产品上的消费都算进去。NarraNexus 在这一层只是读者，没有也无法过滤。

而 NarraNexus **自己**的账本一直存在（`cost_records`，[[cost_tracker.py]] 逐次调用写
入，经 [[agents/cost.py]] 的 `GET /api/agents/_all/costs` 按 viewer 拥有的 agent 读
出），只是此前唯一的出口是聊天页头部的 token 气泡（[[CostPopover.tsx]]）——**离余额
十万八千里**。本组件把它搬到余额旁边。

## 三个不能动的决定

- **金额必须标成估算（`≈`）并说明原因。** `cost_records.total_cost_usd` 用 litellm
  的**公开牌价**计价，而 NetMind 是聚合商、不按厂商直营价出账（[[model_pricing.py]]
  「LIST price, not invoice price」）。所以这个数回答的是「NarraNexus 大概消耗了多少」，
  **永远不能被呈现成能与上面的余额对账**。token 是精确测量的，所以让 token 当主角、
  金额退居说明行。
- **空账本 → 什么都不渲染，不渲染 $0.00。** 与 [[CostPopover.tsx]] 同源的规则：显示
  出来的 0 会被读成「免费」，而不是「太小/未知」。新账号也正处在这个状态，一个空的
  「0」区块纯属噪音。
- **拉取失败必须静默。** 这是一张**讲钱的卡**上的解释性附加区；在里面渲染一行错误，
  用户读到的是「我的钱出问题了」。失败即隐身，绝不能把计费卡带崩。有测试钉住这条。

## 端到端验证抓到、单测没抓到的两件事（2026-08-19）

- **`by_model` 的 key 不是模型 id。** `/api/agents/{id}/costs` 按 `call_type` 折成
  `__main_model__` / `__helper_model__` 两个合成 key（[[agents/cost.py]]），照抄渲染
  就是把裸 sentinel 打到用户屏幕上。映射在 [[tokenFormat.ts]] 的 `shortModelName`，
  i18n key 与 [[CostPopover.tsx]] **复用同两个** —— 同一个桶不能在两个界面有两个名字。

  **单测为什么放行**：fixture 用了"看起来很真"的模型 id。测试替身编造了一个后端从不
  返回的形状，于是它测的是一个不存在的世界。现在 fixture 用真契约，并有一条断言直接
  钉「页面上不能出现裸 sentinel」。

- **必须跟着 focus 刷新。** 用量是在**别处**累积的（agent 在后台跑，花钱的标签页很少
  是这一个）。卡片其余部分本来就有 focus 重拉，只有这一块冻在挂载时刻 —— 一屏活数字里
  唯一一个死的，比不显示更糟。

  初次读取与刷新**在同一个 effect 里**（订阅 + 首读），不拆两个：拆开会踩
  `react-hooks/set-state-in-effect`（在 effect 体里直接调用一个会 setState 的 memoized
  回调），而且订阅与首读本就是对同一个外部系统的同一件事。

## Gotcha

- **token 总数必须含两个 cache 桶。** `input_tokens` 只是全价桶，cache read（0.1x）
  与 cache write（1.25x）是独立列；只加第一个会让一个 cache 命中良好的月份少报一个
  数量级。`?? 0` 防的是旧后端构建没有这些字段时 `undefined` 进求和渲染出 `NaN` ——
  这个坑 [[CostPopover.tsx]] 2026-07-30 已经踩过一次。
- **窗口取 30 天**，对齐上方 NetMind finance 视图的「本月」形状；后端上限 90。
- i18n 用**三参数形式** `t(key, 'default with {{x}}', { x })` —— 面板测试的 i18n mock
  只认第二个参数是字符串的写法，对象形式 `{ defaultValue }` 会退化成渲染出 key。
