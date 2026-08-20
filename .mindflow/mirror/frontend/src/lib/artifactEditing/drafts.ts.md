---
code_file: frontend/src/lib/artifactEditing/drafts.ts
last_verified: 2026-08-20
stub: false
---

# drafts.ts — 编辑草稿层的唯一驻地(#334 r2 I5)

键模板/体积门/删除/陈旧清理收拢一处:store 曾手抄键模板字面量——
同一事实两处拼写,改前缀只会改到一处。无 React(zustand store 消费
removeDraft 不拖 hooks 进模块图)。

## 契约

- `DRAFT_MAX_CHARS = 512K`:量的是 **UTF-16 code unit**(String.length),
  CJK 文档 ≈1.5MB 存储,仍低于 ~5MB 配额(r2 M3:旧名 BYTES 会误导
  调参者)。writeDraft 返回 false=没存上,调用方必须亮
  draftUnavailable,不许吞。
- `sweepStaleDrafts`:每会话一次;**只用正则读 ts**(整份 JSON.parse
  512K 草稿会在编辑面挂载瞬间卡主线程,r2 M4);无 ts 视为陈旧;
  **清理先于恢复是声明的契约**(调用方在 mount 显式先调),不是 effect
  顺序的巧合。刻意不按 artifact 列表删(store 只知当前 agent)。
