---
code_file: frontend/src/components/settings/NetmindAccountPanel.tsx
last_verified: 2026-07-02
stub: false
---

# NetmindAccountPanel.tsx — 「NetMind 账户与订阅」面板（Phase 1 骨架）

## 为什么存在

云端版 Settings 里第一次把用户 NetMind 订阅状态搬到界面（模块 A），并承载沙盒
限免声明（模块 G）。是 B/C/D/E/F 后续模块共用的面板家。刻意**镜像**
[[QuotaPanel]]：cloud-mode 门禁 + 不适用时 `return null`（无布局抖动）。

## 上下游

- 数据源：`api.getSubscription()`（[[api]] → [[billing]] `/api/billing/subscription`）。
- 挂载点：[[SettingsModal]] 的 `billing` 导航 section。
- 文案：`settings.netmind.*`（en/zh locale + 组件内英文默认，铁律 #1）。

## 四态（S0–S3）

- **S0** 本地模式（`mode !== 'cloud-web'`）→ 整个隐藏。
- **S1** `subscription == null` → Free。
- **S2** `status==ACTIVE && auto_renew` → Pro 生效。
- **S3** `status==ACTIVE && !auto_renew` → 已取消、周期内有效、到期降级。

## 踩过的坑 / 设计决策

- **文案必须英文源 + i18n key**（铁律 #1）：组件内联默认用英文，中文在 zh.json。
  最初写成中文内联默认被 code review 拦下（英文用户会看到中文）。
- **billing 的 401 不能触发全局登出**：见 [[api]] `isBillingEndpoint` 跳过——
  否则打开面板（NetMind token 缺失/过期）会把有效 NarraNexus 会话也登出。
- **空 token 早退**：`api.getSubscription()` 无 token 直接 throw，面板落 error 态，
  不发空头round-trip（安全审查 H-1）。
- `resolveState` 把非 ACTIVE 状态（EXPIRED/PAST_DUE/未来态）暂归 free 显示——
  Phase 2 待 NetMind 文档明确后再补专门 UI。
- 余额/消耗（模块 B）不在此 phase：其数据源 user-fee-info 目前 dev 403，是 B 的门禁。
