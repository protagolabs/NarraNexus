---
code_file: frontend/src/components/settings/plugins/PluginFactory.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2d.3）— 工场页（用户插件）

设置 → 插件 里框架安装器下方的第二段：列出 `/api/plugin-factory` 的插件（状态芯片按内核状态机、warning、
隔离原因、provides）；按 `owner/repo[@tag]` / `owner/repo#ref` / 本地目录安装；安装返回的 `permissions` 非空
时弹披露卡，用户「我了解」才 `acknowledge-permissions`，否则 disable（spec §10.4 首次启用第三方必须明示）；
enable/disable/upgrade/uninstall（`protected` 插件不能停用/卸载）；LKG 回滚；安全模式横幅 + 二分向导
（good/bad/stop）；每插件错误环（errorSink 上报的）。每个变更后提示「重启生效」——插件在启动时加载，页面
不假装热生效。云端整段隐藏。
