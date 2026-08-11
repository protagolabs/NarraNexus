---
code_file: frontend/src/components/settings/PrivacySettings.tsx
stub: false
last_verified: 2026-08-11
---

# PrivacySettings.tsx — "哪些数据离开这台机器"的两个开关

## 为什么存在

两个**作用域刻意不同**的同意,文案与行为都不许混淆:

- **产品分析**:per-USER,DB 行(`/settings/analytics`)。后端 06-08
  就有,UI 一直困在未挂载的 SettingsModal 里——本组件是它第一次
  真正可达的家;
- **诊断遥测**:per-MACHINE 标记文件(`/settings/telemetry`)。
  `controllable=false`(env 覆盖或多租户云)时开关**禁用并说明**,
  而非隐藏——消失的开关读作"没有遥测",那是假话。

## 设计决策

- 两个开关都乐观翻转、失败回滚(沿用 analytics 在 modal 里的模式);
- 遥测行的 note 二选一:不可控 → managed 说明;可控 → 时效说明
  (关闭数秒生效 / 开启下次启动生效)——不对称性必须写给用户看;
- 组件不自带 SectionHeader,由 SettingsPage 的 privacy 分支包裹
  (与 ModelDefaultsSettings 同款分工)。

Tests: `__tests__/PrivacySettings.test.tsx`(翻转/回滚/禁用守卫)。
