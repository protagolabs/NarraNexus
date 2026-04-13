---
code_file: frontend/src/components/dashboard/QueueBar.tsx
last_verified: 2026-04-13
stub: false
---

# QueueBar.tsx — Intent

## 为什么存在
一眼看见 agent 所有 job 在 6 种 live state 的**比例分布**。比纯数字列表（`2R 5P 1B 1F`）更直观，比饼图更省空间。

## 上下游
- **上游**：
  - `AgentCard.tsx::OwnedCard` inline 区（collapsed 默认也显示，compact mode）
  - 未来可能在 expanded 区用 full mode
- **数据**：`agent.queue: QueueCounts`（6 state + total，服务端 `derive_queue` 产出）

## 设计决策
1. **Stacked bar 而非 donut / pie**：dashboard 里 bar 更适合水平展示 + 多 agent 并排对比。饼图占正方形空间且不利于多卡片 side-by-side 比较。
2. **Compact mode vs Full mode**（两种渲染）：
   - Compact：内联在卡片头部，16px 宽、高 1.5px、只显示总数 + 异常指示（failed/blocked）
   - Full：在 expanded 区，100% 宽、完整图例
3. **空 queue 返 null**（v2.1.1 改进）：之前渲染 "Queue · empty" 是视觉噪声，直接不显示更干净。
4. **Segment 颜色和 `JobsSection.STATE_META` 对齐**——用户看到 bar 的红段能和 JobsSection 里的 failed job 条目颜色联想起来，形成一致语义。
5. **点击 segment（未来）**：目前只是 title tooltip 显示 "N failed" 等；将来可触发"过滤 JobsSection 只看该 state"——预留了 data-testid。

## Gotcha
- `ORDER` 数组决定从左到右的渲染顺序（running 在最左，failed 在最右）。**这不是自由选择**——和人眼对"紧迫度"的理解（越右越严重）有关；改顺序前想好是不是真要变。
- `LABEL_SHORT` 的"running"/"active"/"pending"/"blocked"/"paused"/"failed" 是 i18n 敏感的——未来做国际化要走 intl 库而不是硬写英文。
- `width: ${pct}%` 的累加可能因浮点误差总和 != 100%（比如 99.8% 或 100.1%）——视觉可接受，不修。如果对齐精度重要，可做"最后一段填到 100%"的修正。
- **compact mode 的"只显示失败和阻塞"逻辑**硬编码在组件里——如果未来想也显示 paused 的小红点，要改这里。
