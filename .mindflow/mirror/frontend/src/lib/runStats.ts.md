---
code_file: frontend/src/lib/runStats.ts
last_verified: 2026-08-28
stub: false
---

# runStats.ts — 运行统计行的非组件辅助

## 为什么存在

存在的理由说白了是一条 lint 规则：`react-refresh/only-export-components` 要求
组件文件只导出组件，而 [[RunStatChips]] 需要向外提供两样非组件的东西——
`formatDuration` 和 `hasRunStats`。它们搬到这里，组件文件就只剩组件。

**为什么不并进 [[tokenFormat]]**：那个文件是**用量数字**的 SSOT（求和口径、
token 格式、USD 格式、模型标签），被成本 popover 和账号用量面板共用。时长格式
和"这一轮有没有统计可显示"的谓词不属于那套规则，塞进去会让一个名字叫
tokenFormat 的文件承担越来越杂的东西。

## 上下游

- **被谁用**：[[RunStatChips]]（两者）、[[InnerThoughtCard]]（`hasRunStats`，
  用来决定整块 RunMeta 是否折叠）
- **依赖谁**：[[tokenFormat]] 的 `inputSideTokens`——输入侧口径只有一处

## 设计决策

**`hasRunStats` 让调用方能提前折叠。** 组件在无数据时自己返回 null 就够渲染
了，但调用方需要**提前**知道，才能连外层容器一起收掉，否则会出现"边框还在、
里面空了"的空壳。

**谓词与渲染是两份手写副本——这是已知约束，不是结构保证。**（本条修正
2026-08-28 初版里"共用一套条件"的说法，那给了错误的安全感。）真正共享的是
其中**已经被写错过的**那两条：`hasCostToShow` 和 `hasTokens` 抽成函数，两边
都调。其余条件（state / duration / models）仍是各写一遍。加新 chip 时**两处
都要改**；漏改的表现是"只有这一项数据"的轮次整行不渲染，静默且难查。没有上
registry 抽象是因为目前只有 5 枚 chip，那套间接层的成本高于它防住的东西。

## Gotcha

**`hasCostToShow` 门在 `> 0` 而不是 `!= null`。** 记成 0 的账不是"这轮很便
宜"，是"我们不知道价格"：`price_for` 对定价表不认识的 model id 返回 None，
`calculate_cost` 于是全 0 —— 本地库里这是**多数**（写下这条时 2384 行中
1837 行，全是主力的 DeepSeek / GLM id）。而 [[tokenFormat]] 的 `formatCost`
自述契约是「调用方已按 > 0 把门」，它对极小值返回 `<$0.0001`。两者相接的结果
就是把"不知道价格"渲染成"花了一点点钱"。后端 `_build_event_meta` 在没有账目
行时让 `total_cost_usd` 保持 None、好让 UI 藏掉 chip，是同一个判断。

**判断输入侧用 `inputSideTokens(meta)` 而不是 `meta.input_tokens`**：后者只是
全价桶，缓存命中的一轮里 cache read/write 能占输入侧 99% 以上，只看它会把一轮
真实有消耗的运行判成"没有统计"。
