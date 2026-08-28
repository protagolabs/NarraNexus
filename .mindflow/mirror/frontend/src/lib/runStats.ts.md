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

**`hasRunStats` 与渲染共用一套条件。** 组件在无数据时自己返回 null 就够了，
但调用方需要**提前**知道，才能连外层容器一起折叠，否则会出现"边框还在、里面
空了"的空壳。谓词和 chips 的渲染条件写成同一组判断，两者不会各说各话。

## Gotcha

判断输入侧用 `inputSideTokens(meta)` 而不是 `meta.input_tokens`：后者只是全价
桶，缓存命中的一轮里 cache read/write 能占输入侧 99% 以上，只看它会把一轮
真实有消耗的运行判成"没有统计"。
