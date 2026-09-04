---
code_file: backend/plugins_factory/routes.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2c）— `/api/plugin-factory`

独立前缀，避免与框架安装器 `/api/plugins/{id}/install` 撞路由；不在鉴权豁免名单（401 fail-closed）。
list/index/install/enable/disable/acknowledge-permissions/uninstall/upgrade/update-check/rollback/
safe-mode/leave/bisect/{start,answer,stop}/errors（GET/POST）/assets（带 `X-Content-Integrity` SRI 头）。
`set_service` 让测试注入临时 home 上的 service；`main.py` 把启动报告塞给它。`routes*.json` 快照只多这组路由。
