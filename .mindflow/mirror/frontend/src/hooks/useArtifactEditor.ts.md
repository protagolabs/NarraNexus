---
code_file: frontend/src/hooks/useArtifactEditor.ts
last_verified: 2026-08-19
stub: false
---

# useArtifactEditor.ts — 常驻编辑面状态机

## 为什么存在

Spec A §3/§4 的编辑状态机,与 UI 解耦(ResidentTextEditor 是壳,
md 块编辑器 C3 复用):load(bytes→text+hash)→ dirty → save(锁)→
409 二选(overwrite/discard)。

## 两条防丢规则(都是「击键永不静默消失」)

1. **dirty 编辑器忽略外部刷新**:agent 改文件→updated_at→raw url
   重铸→若 reload 会砸掉用户正打的字。dirty 时跳过 reload(AionUI
   dirty-skip 同构),分歧推迟到保存时的 409,由用户决定。
2. **localStorage 草稿**:setText 直写 `narra:artifact-draft:{id}`,
   重挂载且盘上 base 未变时恢复(dirty+draftRestored 横幅);base 变了
   =机器上无法合并,丢弃草稿(如实的取舍,别改成静默保留)。

## 坑

- 锁 base=加载字节的 hash,**不是** artifact.content_hash(表可能落后
  于盘);保存成功后用响应的 content_hash re-base。
- textRef/dirtyRef:save 回调不随击键换身份;load effect 不把 dirty
  当依赖(url 变才触发)。
