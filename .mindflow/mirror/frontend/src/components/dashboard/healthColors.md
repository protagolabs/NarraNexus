---
code_file: frontend/src/components/dashboard/healthColors.ts
last_verified: 2026-04-13
stub: false
---

## v2.2 改动（2026-04-13）

- 新 `AgentHealth` 成员 `acknowledged`：slate gradient rail，**仅 frontend 派生**（服务端永不发此值）
- 全部 rail 升级为 `bg-gradient-to-b from-X-400 to-X-600` + `shadow-[inset_-1px_0_2px_...]`（G4 视觉精致化）
- 新增 `acknowledgedHealthOf(health, allDismissed, kind)` helper：banner 全 dismiss 时 error→acknowledged（永不 healthy，Security-M1）/ warning/paused→healthy
- **不变量**：任何后续修改不允许让 error 在 allDismissed=true 时返回 healthy_*。这是视觉社工防线

# healthColors.ts — Intent

## 为什么存在
把"`AgentHealth` → tailwind class"映射集中一处。换色系改一个文件就全 dashboard 统一，而不是 grep 10+ 组件里散落的颜色字符串。

## 上下游
- **被引用**：`AgentCard / Sparkline / `（间接）`DashboardSummary`。理论上 `QueueBar / JobsSection / StatusBadge` 也该从这里读，但目前各自硬写一份（见 Gotcha）。
- **依赖**：`AgentHealth` 类型（来自 `types/api.ts`）

## 设计决策
1. **4 个维度**的 class：`rail`（左侧竖条）、`cardTint`（整卡轻染色）、`text`（文字强调色，用于 verb_line）、`accent`（sparkline / badge）。分层是因为不同视觉位置需要不同饱和度——rail 要显眼，cardTint 要极淡。
2. **Dark mode 支持**：所有 text class 都含 `dark:` 变体。Tailwind 自动按 class name 生效。
3. **`idle_long` 只加 `opacity-75`**：不改颜色，只淡化——用户视觉上"这些 agent 可以忽略"。
4. **`error` 的 `cardTint: 'bg-red-500/5'`**：非常淡的红色背景。强到能感知"这张卡不一样"，弱到不刺眼。

## Gotcha
- **色彩重复定义问题**：当前 `QueueBar.SEGMENT_CLS` / `JobsSection.STATE_META` / `DashboardSummary.CHIP_ORDER.dotCls` 各自硬编码颜色——不是从这里读取。意味着想改"所有绿色"要改 4 个文件，而不是 1 个。**技术债**，将来重构应统一到这里。
- **`bg-red-500/5` 要 Tailwind JIT 识别**——Vite 配置里 content glob 要覆盖这个文件。目前 `frontend/tailwind.config.js` 应该已经 glob `src/**/*.{ts,tsx}`，默认 OK。
- 未来加新 `AgentHealth` 枚举值**必须**在这里加一行——否则 `HEALTH_COLORS[newValue]` 是 undefined，解构时 rail/cardTint 变 undefined，UI 崩。TS 能在增 union 时报错（`Record<AgentHealth, ...>` 强制完整覆盖），但新增时要记得来这。
