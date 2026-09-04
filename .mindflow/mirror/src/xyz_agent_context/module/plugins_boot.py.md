---
code_file: src/xyz_agent_context/module/plugins_boot.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2b.5）— mcp / workers 进程的插件启动

只登记声明式贡献（tools / mcp servers / skills / workers），不激活用户插件代码（激活需要 backend 的设置
store、服务定位器、事件总线），不注册插件表（backend 拥有迁移）。每角色一个启动标记；到达即视为健康。
注册表已冻结（同进程二次调用、测试）时返回空报告。

## 2026-09-04 · ingress triggers (batch 3c.3)

`boot_channel_plugins()` — the standalone channels supervisor boots the `workers` role so `CHANNEL_TRIGGER_MAP` (a registry view) is populated and overrides applied before any channel starts.
