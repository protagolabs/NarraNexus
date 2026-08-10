---
code_file: backend/routes/channels/narramessenger.py
stub: false
last_verified: 2026-06-18
---

> 2026-08-10:`_verify_agent_ownership` 不再是本文件定义——模块级别名指向
> `backend/routes/_ownership.py::check_owned`(canonical;DB 故障走 503 而非 200)。


## Why it exists

The frontend "paste the bind link" entry point for NarraMessenger:
`GET /api/narramessenger/credential`, `POST /bind`, `POST /unbind`. Mirrors
`backend/routes/channels/lark.py` (same `_verify_agent_ownership` local-vs-cloud pattern).

## Design decisions

- **All real work lives in `_narramessenger_service.do_bind` / `do_unbind`** —
  shared with the `narra_bind` MCP tool, so the chat path and the dashboard path
  bind identically. The route is a thin auth + validation wrapper.
- `/credential` returns the sanitised `get_public()` view (NO bearer token);
  `data` is null when unbound — which is what `IMChannelsSection.fetchConnected`
  keys on for the ✓/not-bound badge.
- Registered in `backend/main.py` under `/api/narramessenger`.
