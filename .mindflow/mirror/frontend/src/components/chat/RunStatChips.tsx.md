---
code_file: frontend/src/components/chat/RunStatChips.tsx
last_verified: 2026-08-28
stub: false
---

# RunStatChips.tsx — 一轮运行的统计药丸行

## 为什么存在

两个界面要回答同一个问题「这一轮花了多少」：Inner Thoughts 的运行卡片
（[[InnerThoughtCard]]）和 Conversation 的消息气泡（[[MessageBubble]]）。
2026-08-28 给气泡加单轮用量时，本可以把卡片里那段 chips 复制一份——但
[[tokenFormat]] 存在的全部理由就是这种复制：同一轮在一个屏上读 2.4M、另一个
屏上读 2.40M，读者无从判断哪个是四舍五入过的，而且**它已经真实发生过一次**
（2026-07-30，一周 1.2M token 显示成 "input 213"）。所以抽成组件，而不是复制。

## 上下游

- **被谁用**：[[InnerThoughtCard]]（卡片里 input/output 文本块之上）、
  [[MessageBubble]]（disclosure 之上）
- **依赖谁**：[[tokenFormat]] 的 `inputSideTokens` / `formatTokens` /
  `formatCost`；数据来自 `/event-log` 响应的 `meta`
  （后端 `chat_history._build_event_meta`）

## 设计决策

**格式规则以 `lib/tokenFormat` 为准。** 抽取时两边规则不同，必须裁决：共享库
是 SSOT，卡片的私有副本作废。代价写在文件头注释里——M 档多一位小数，极小额
从 `$0` 变 `<$0.0001`（`$0` 读起来像免费，正是共享库当初要防的）。
`formatDuration` 没有共享孪生体，作为唯一的本地规则留在这里。

**`hasRunStats` 单独导出。** 组件自己在无数据时返回 null 就够渲染了，但调用
方需要**提前**知道，才能决定整块容器是否折叠——卡片的 `RunMeta` 要判断
「chips 和 input/output 都没有就整个不渲染」。谓词和渲染共用一套条件，不会
出现"容器还在、里面空了"的空壳。

## Gotcha

- `inputSideTokens` 而非 `meta.input_tokens`：后者只是全价桶，缓存命中的一轮
  里 cache read/write 占输入侧 99% 以上。
- `meta.models` 直接渲染真实模型 id；这里**不是** `/costs` 那种
  `__main_model__` / `__helper_model__` 合成键，不要套 `shortModelName`。
