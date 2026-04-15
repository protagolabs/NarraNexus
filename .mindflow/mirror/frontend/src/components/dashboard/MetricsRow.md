---
code_file: frontend/src/components/dashboard/MetricsRow.tsx
last_verified: 2026-04-13
stub: false
---

# MetricsRow.tsx — Intent

## 为什么存在
卡片底部**今日数字总结**——成功次数、失败次数、平均时长、成本。一行轻量的"量化健康"。

## 上下游
- **上游**：`AgentCard.tsx::OwnedCard` inline 区（collapsed 默认也显示）
- **数据**：`agent.metrics_today: MetricsToday`（runs_ok / errors / avg_duration_ms / avg_duration_trend / token_cost_cents）

## 设计决策
1. **null → em-dash**（`—`），**不**显示 0：metrics 可能"真是 0"也可能"没数据源"。区分很重要。当前 `avg_duration_ms` 和 `token_cost_cents` 都是 null（后端没数据源），前端正确渲染 `—` 而不是 0（否则用户以为响应时长真的是 0ms）。
2. **`errors > 0` 字体红色 + 加粗**：唯一一个视觉强调项。其他数字正常颜色——只在 failure 时拉警报，其他时候安静。
3. **avg trend 箭头**：`up / down / flat / unknown` → `↑ / ↓ / · / ''`。目前 trend 始终 `unknown`（后端没历史数据对比）；UI 就位，等数据来。
4. **成本格式化**：`cents → $0.12` 的转换在前端做（而不是后端传已格式化字符串）——保持后端 API 语义干净（int cents），前端控制展示格式（未来支持多币种切换）。
5. **font-mono**：数字对齐看起来整齐。中文/英文混排字体下纯等宽更易扫。

## Gotcha
- `token_cost_cents` 和 `avg_duration_ms` 作为 owner-only 敏感数字——`PublicAgentStatus` 根本不包含 `metrics_today` 字段。这里默认 `agent.metrics_today` 一定存在（因为 owned 才会渲染这个组件）。类型系统保证，没 null 检查。
- `formatCost` 处理边界：<100 cents 返 `$0.XX`，>=100 正常拆小数点。`$0.00` 不显示特殊（没 0 case 的优化）。
- `avg_duration_trend` 的值有 `flat` 我用了 `·` 作为箭头——视觉不明显。若未来想强调 flat（"和上周一样"），换 `→` 或 emoji。
- **没有 tooltip 解释字段**——"`⏱ 2.1s`" 对非技术用户可能不直观。可加 `title` 属性。
