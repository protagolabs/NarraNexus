---
code_file: frontend/src/components/dashboard/DurationDisplay.tsx
last_verified: 2026-04-13
stub: false
---

# DurationDisplay.tsx — Intent

## 为什么存在
把 `started_at` 格式化成 "Xs / Xm / XhYm" 人话。所有需要显示"持续了多久"的位置（card header、running job 的 "started 12s ago"）复用。

## 两个导出
- `formatDuration(totalSec: number | null): string` — 纯函数，可单测
- `DurationDisplay({ startedAt })` — 组件，读当前时间计算 delta

`formatDuration(null)` → em-dash (`—`)；组件内 `startedAt === null` → em-dash。

## 已知不雅
1. **`Date.now()` 是 impure**。React `hooks/purity` lint 报 "Cannot call impure function during render"。文件内用 `// eslint-disable-next-line react-hooks/purity` 豁免
2. **不自动 tick**。组件不自起 setInterval——靠 `DashboardPage` 的 polling（3s/30s）触发整卡重渲染，duration 顺带更新。代价：非 running 态时 "idle 12m ago" 的文字有最多 30s 延迟。接受这个 trade-off 以避免每张卡一个 timer
3. **`formatDuration` 作为命名 export** 触发 `react-refresh/only-export-components`——加了豁免注释

## 数据契约
输入 ISO8601 字符串。不做时区转换——`new Date(iso)` 按浏览器本地时区解读。后端目前返回 naive datetime 转 ISO（没 `+00:00`），前后端时区一致时无误差；跨时区部署需要后端改 timezone-aware（见 `dashboard.md` Gotcha）。

## Gotcha
- **`formatDuration(null)` vs `formatDuration(0)` 行为不同**——0 → `"0s"`（确实 0 秒前），null → `"—"`（没有起始时间，如 idle agent）
- **刷新粒度受上游 polling 限制**——秒级误差可接受。如果未来需精确跳秒（比如倒计时），得给组件加自己的 interval，当心 cleanup
- **em-dash 用 `'\u2014'` 常量**——不要改成 ASCII `-`（视觉差距明显）；不要写字面量 `—`（部分编辑器/终端对 BMP 外字符不友好）
