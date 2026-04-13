---
code_file: frontend/src/components/dashboard/DashboardSummary.tsx
last_verified: 2026-04-13
stub: false
---

# DashboardSummary.tsx — Intent

## 为什么存在
**图例 + at-a-glance 的二合一**（v2.1.1）。在页面顶部显示"共 N 个 agent · X running · Y idle · Z error"，每个 chip 的颜色点就是卡片左侧 rail 的颜色——用户**读一次就学会**了颜色语义，不需要额外的 "Legend" 按钮。

## 上下游
- **上游**：`pages/DashboardPage.tsx` 在 agents 列表上方渲染（agents 非空时）
- **数据**：遍历 `agents[]`，按 `health`（owned）或默认 `healthy_idle`（public 非自有）分组计数

## 设计决策
1. **Chip 即图例**：每个 chip 有 tooltip（`TOOLTIPS[health]`）解释这个健康级别的含义。用户悬停即学会，不用跳文档。
2. **为 0 的 health 桶隐藏**：只显示**有 agent 的**桶，页面不被空 label 填满。
3. **公有变体计数并入 `healthy_idle`**（因为 `PublicAgentStatus` 没有 `health` 字段——权限边界不给）。这是务实妥协：公有 agent 对非 owner 来说本来就没有完整健康信息，按"外观上健康闲置"分类。
4. **"hover any dot for meaning" 提示**：右侧一行 italic 小字，**显式**引导用户把鼠标移到 chip 上看 tooltip。不指示的话很多用户不会发现 title 属性。
5. **`total` 独立显示**在最左："5 agents"——总量信息优先于分类信息。
6. **零 agent 时 return null**：不显示"0 agents"空条——页面下面已经有 "No agents yet" 空态组件。

## Gotcha
- **`CHIP_ORDER` 固定**从"好 → 坏"排列（running / idle / blocked / paused / error / quiet）。如果文化上用户更关心先看问题（error 在前），顺序改了这里就行。
- `TOOLTIPS` 当前只英文——i18n 时整体走 intl 库。
- **和 `HEALTH_COLORS` 的颜色约定**是独立硬编码的（这里有自己的 `dotCls`）——如果 `healthColors.ts` 改色，这里也要同步。**最好**以后把 DashboardSummary 的 dot 颜色也从 `HEALTH_COLORS[h].accent` 读取，避免两处各自一份。
- **不随 polling refresh**——依赖 `pages/DashboardPage::agents` state，polling 更新 agents 时 props 变 → 组件自然 re-render。
