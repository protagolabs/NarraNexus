---
code_file: frontend/src/components/settings/ApplyDefaultsToAgentsDialog.tsx
last_verified: 2026-08-26
stub: false
---

## 2026-08-26 — 保存默认后「应用到所有 agent」确认框

从 [[ModelDefaultsSettings]] 保存 owner 默认成功后弹出（仅当旗下 agent 存在
覆盖）。按槽（agent / helper_llm）勾选，每槽显示受影响 agent 数；某槽零覆盖
则 checkbox 置灰。确认 = 调 `api.applySlotsToAgents(slots)`（clear-to-inherit，
删除这些 agent 的 per-agent 覆盖，使其回到继承新默认）；「仅保存默认」= 只
关闭、不动 agent。文案明示对运行中 agent 下次解析生效、不打断当前运行（守
铁律 #14/#15，平台不做打断源）。复用 `@/components/ui` 的 `Dialog`
（`isOpen`/`onClose`/`title`/`size`）+ `DialogContent`/`DialogFooter`。
