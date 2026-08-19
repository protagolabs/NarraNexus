---
code_file: frontend/src/lib/mock/fixtures.ts
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — `mockCostSummary.by_model` 改回后端真实契约

原来的 key 是三个真实模型 id（`claude-sonnet-4.6` 等），而
`GET /api/agents/{id}/costs` **从不返回模型 id** —— 它按 `call_type` 折成
`__main_model__` / `__helper_model__` 两桶（正本见 [[tokenFormat.ts]]）。

这不只是"数据不准"。同一周里，[[NarraUsageSection.tsx]] 的作者正是照着一个编造的
形状写渲染代码，把裸 `__main_model__` 打到了真实账户页上。**mock 往往是新人最先
看到的那个"契约"**，让它继续编造，就是把同一个坑留在最显眼的位置。

两桶的分项数值仍与 `total_*` 合计精确对齐（改 key 时一并核过）。

# mock/fixtures.ts — demo / 离线模式的静态数据集

## 为什么存在

[[index]](mock/index.ts)的桩方法返回的数据全部来自这里:demo 部署和无后端
本地开发时,UI 全链路渲染所依赖的 agents / jobs / cost / awareness 等假数据。
契约与 index.ts 相同:**类型必须跟 [[api]] 的响应类型同步**——types/api.ts
的接口加了必填字段,这里的 fixture 不补就会 tsc 失败(这也是本文件的
类型安全网:fixture 是前端类型的第一个"消费者")。

## 变更史

- 2026-07-30 — `mockCostSummary` 补 `cache_read_tokens` /
  `cache_creation_tokens`(total / by_model / daily 三层),跟随 CostSummary
  加缓存桶字段。数值按真实形态配比:缓存读远大于未缓存输入,demo 里
  popover 的"含缓存读/写"行才可见。

## 坑

daily 数据用 `Math.random()` 生成,断言 fixture 数值的测试不要指望它稳定。
本文件 2026-07-30 前没有 mirror,变更史从当日起记。
