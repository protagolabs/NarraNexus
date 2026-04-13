---
code_file: frontend/src/components/dashboard/JobsSection.tsx
last_verified: 2026-04-13
stub: false
---

# JobsSection.tsx — Intent

## 为什么存在
Owner 视图里 Agent 的 job 队列可视化——覆盖**所有 6 种 live state**（running / active / pending / blocked / paused / failed），每个 job 可单独展开查看详情 + 内联操作（Retry / Pause / Resume）。

## 上下游
- **上游**：`AgentCard.tsx::OwnedCard` expanded 态时渲染
- **下游**：
  - `expandState.ts::useExpanded` 做 section 级和 item 级展开状态
  - `lib/api.ts`: `getJobDetail` / `retryJob` / `pauseJob` / `resumeJob`（懒加载详情 + 三个 mutation）
- **数据**：`agent.running_jobs[]` + `agent.pending_jobs[]`（含 `queue_status` 字段区分 pending/active/blocked/paused/failed）

## 设计决策
1. **两层 expand**（progressive disclosure）：
   - Section 级：section header 点击展开/折叠整个 job 列表
   - Item 级：点单个 job 行展开其详情面板（lazy load via getJobDetail）
2. **State-specific 视觉**：`STATE_META` 映射每个 state 到图标 + 颜色 + 标签。6 种颜色和 `QueueBar` 的 segment 颜色一致（绿/蓝/灰/橙/黄/红）。
3. **按钮条件渲染**：
   - `failed` → `[Retry]`
   - `active` / `pending` → `[Pause]`
   - `paused` → `[Resume]`
   - 其他 state 无按钮。避免"点了无效操作"。
4. **Running jobs 和 pending jobs 分两组渲染**：running 先显示（视觉优先级），pending 后。Pending 组内部按 `queue_status` 的自然顺序展示。
5. **Lazy detail fetch 只在首次展开时**：`if (!expanded && detail === null && !loading)` 守卫，防止反复点击重复请求。
6. **Action 按钮成功后 `setDetail(null)`**：下次展开会重新 fetch 最新状态。不自动 refetch 主 dashboard——polling 的下一 tick 会同步。

## Gotcha
- **stopPropagation 链**：JobItem onClick + 每个 action 按钮 onClick 都必须 `e.stopPropagation()`，否则冒泡到 AgentCard body click 会顺便展开/折叠整卡（反直觉）。
- **Action 失败的 UX 反馈弱**：目前只在按钮文字上加 "failed"；没有 toast 或详细错误说明。将来加 toast 系统时要改。
- **`JobDetailBody` 对 `trigger_config` 用 `String(...)`**：后端这个字段是 JSON（cron 配置等），直接 String 会出 `[object Object]`。只有在 cron 字符串时好看。接入后端时要规范为字符串化的 trigger summary。
- **`progress` 字段前端渲染简化**——只显示 `step N/M`，没画进度条。因为后端目前 `RunningJob.progress` 一直 None（没从 `instance_jobs.process` JSON 字段解析）。未来如果解析出步骤 → UI 这里要加 `<progress>` bar。
- `STATE_META.running` 的 key 和类型里的 `'running'` 字面值必须同步；改 state 枚举 checklist：`_dashboard_helpers._LIVE_JOB_STATES` + `JobQueueStatus` type + `STATE_META` + `SEGMENT_CLS in QueueBar` 全部要改。
