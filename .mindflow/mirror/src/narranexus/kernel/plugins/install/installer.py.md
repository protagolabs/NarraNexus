---
code_file: src/narranexus/kernel/plugins/install/installer.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2c）— 安装流水线

fetch 到 plugin home 下的 staging → manifest 按本宿主校验（不满足时若有 `versions.json` 提示该装哪个 tag）→
黑名单 → 已装且非 replace 拒绝 → eager 依赖装进 staging/pyenv → copy 模式整目录搬到 `plugin_dir(id)`
（旧版本先改名再删）、link 模式原地 → 最后才写 registry（写前 LKG 快照）。任一步失败：staging 清理、registry
不动（测试钉住「依赖失败不落账」）。`uninstall` 只删 plugin home 之内的 copy 目录与 link 模式的私有依赖；
`check_update` 查 GitHub latest release；`upgrade` 按原来源重装（replace）。
