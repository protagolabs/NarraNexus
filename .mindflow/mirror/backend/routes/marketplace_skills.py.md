---
code_file: backend/routes/marketplace_skills.py
last_verified: 2026-07-21
stub: false
---

# routes/marketplace_skills.py — /api/marketplace/skills/*

Skill Marketplace API. The `/api/marketplace` namespace is split by object:
this router owns `skills/*`; `teams/*` is RESERVED for the Team/Agent bundle
marketplace (unmerged `feat/in-app-marketplace`) — never claim it here.

## Auth model

Like a package registry: read endpoints (search without agent_id, detail,
batch updates via ?skills=, download) are PUBLIC; workspace-touching
endpoints (install, agent-scoped search annotation/updates) resolve identity
via auth_middleware; publish is gated by the `MARKETPLACE_PUBLISH_TOKEN` env
+ `X-Publish-Token` header (403 when unset — publish is closed by default).

## Route-order gotcha

FastAPI matches in registration order: `/search`, `/updates`,
`/{skill_id}/download` are declared BEFORE `/{skill_id}` on purpose.

## Contract details

- install 409 body: `{"code": "SKILL_ALREADY_INSTALLED"}` (spec §6);
  scan-rejected installs → 400 with the human-readable reason (logged
  server-side with agent/user/skill for prod debugging).
- publish 422 body carries `scan_report` (list of issue dicts) so the
  developer sees exactly which rules fired.
- /download responds with `X-Skill-Version` / `X-Package-Hash` headers —
  the desktop RemoteMarketplaceSource reads them; the temp file is cleaned
  by a Starlette BackgroundTask after the response streams.
- /download increments the download counter (desktop installs are counted
  here; cloud installs are counted by LocalMarketplaceSource).
