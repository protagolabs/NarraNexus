---
code_file: frontend/src/components/dashboard/RecentFeed.tsx
last_verified: 2026-04-13
stub: false
---

# RecentFeed.tsx — Intent

## 为什么存在
每个 agent 的"最近做过什么"——比 metrics_today 的聚合数字更具体。默认折叠（避免卡片臃肿），点开看 3 条最近 events。

## 上下游
- **上游**：`AgentCard.tsx::OwnedCard` expanded 态 + `recent_events.length > 0` 时渲染
- **下游**：`expandState.ts::useExpanded` 管 section 展开
- **数据**：`agent.recent_events[]`（最多 3 条，服务端 `fetch_recent_events` → `build_recent_events_resp` 整形）

## 设计决策
1. **默认 collapsed**：大多数时候用户不需要看——"健康 agent + 偶尔扫一眼"模式。
2. **每条一行 + 图标 + 颜色**：图标（✓ / ▶ / ⚠ / 💬 / ·）在视觉上区分 event kind，颜色在 kind color map 里。方便快速扫。
3. **时间用本地化的 HH:MM**：`toLocaleTimeString` 按用户时区。不显示日期（3 条都是"最近"，默认今天）。
4. **verb + target 串联**：后端 `build_recent_events_resp` 已把原始 event 分类 + 生成人话 verb；前端只拼 `${verb}: ${target}`。逻辑服务端做。
5. **limit 3 硬编码**：4 条会挤，2 条信息不够。3 是视觉甜点。

## Gotcha
- **event.kind 分类是启发式**：后端看 `final_output` 含不含 "ERROR"/"Error" 判 failed；没做的更精细的分类（比如 partial failure / timeout vs exception）。短期够用，将来有专用 `event.status` 列再替换。
- **`duration_ms` 字段在 `RecentEvent` 里定义了但目前始终 null**——events 表暂无 duration 列。UI 也没显示。未来扩 schema 时启用。
- **no drill-down**：点 event 条目目前没跳转。合理的下一步是跳 event 详情页（event_log 可视化），但那需要有这个页面先——TODO。
- **不刷新**：events 列表 3s polling 会更新，但用户在 expanded 态查看时看到新 events "插入"顶部可能反直觉。没 animation 处理；可接受。
