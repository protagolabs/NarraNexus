---
code_file: frontend/src/components/dashboard/StatusBadge.tsx
last_verified: 2026-04-13
stub: false
---

# StatusBadge.tsx — Intent

## 为什么存在
把后端 `AgentKind`（8 种：idle + 7 种 WorkingSource）映射到统一视觉：图标 + 颜色 + 英文标签。Dashboard 所有需要显示 kind 的地方（卡片 header / Recent feed / 未来的 Timeline）都走这个组件——**视觉一致性单一来源**。

## kind → 视觉映射表
```
idle         🌙 Moon           text-secondary   "Idle"
CHAT         💬 MessageCircle  emerald-500      "Chat"
JOB          ⚙️ Briefcase      amber-500        "Job"
MESSAGE_BUS  📡 Radio          sky-500          "Bus"
A2A          ↔️ ArrowLeftRight violet-500       "A2A"
CALLBACK     ☎️ PhoneCall      rose-500         "Callback"
SKILL_STUDY  🎓 GraduationCap  blue-500         "Skill"
MATRIX       🧪 FlaskConical   fuchsia-500      "Matrix"
```

图标来自 `lucide-react`。颜色选择原则：邻近 kind 取不相邻色相，减少"都是一种色系"的混淆。

## 可测试性
每个 variant 有 `data-testid={`status-badge-${kind}`}`——Playwright / RTL 可精确定位。G001 验收断言用此 test-id 确认 7 种 kind 图标都能渲染。

## 数据契约
单 prop `kind: AgentKind`。类型来自 `types/api.ts`，和后端 `Literal` 严格对齐——后端加新 kind 时 TS 编译强制 ICON_MAP 更新。

## Gotcha
- **`idle` 不是 WorkingSource 成员**，但它是前端视角的 kind（后端 `classify_kind(None)` 返回 'idle'）。ICON_MAP 包含它；其他 kind-based switch 也不要漏 idle 分支
- **扩展新 kind 只改本文件的 ICON_MAP**——其他组件读 `status.kind` 是类型安全 narrow（discriminated by kind）。但 action_line 生成在后端 `humanize_verb` / `build_action_line`，加 kind 时两边都要改
- lucide 图标大小统一 `w-3 h-3`——别改单个图标尺寸破坏对齐
