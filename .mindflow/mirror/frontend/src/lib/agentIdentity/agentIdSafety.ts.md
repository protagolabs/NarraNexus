---
code_file: frontend/src/lib/agentIdentity/agentIdSafety.ts
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 移入 `lib/agentIdentity/`

与 agentIdSafety / defaultAgent / guideAgent / onboardingGate 同组：「这个 agent id 能不能用、
该选哪个 agent、哪个是引导 agent、用户是否还欠欢迎流程」。原先平铺在 67 个文件的 `lib/` 里，
按 `netmindAuth/` 先例收成子目录（无 index 桶文件，直接按模块路径导入，`vi.mock` 目标保持精确）。

# lib/agentIdentity/agentIdSafety.ts — ids that can never be a real agent never leave the browser

## Why it exists

The backend refuses an agent id that is not exactly one path segment
(`workspace_paths.safe_id_segment`: empty, NUL, `/`, `\`, or dot-led such as
`..`) and logs it as a signed tenant-boundary violation. The edge monitor
suspends the account on the FIRST such line from a person's session and bans
the IPs it was recently active from. That is right for a prober, but a crafted link
(`/app/agents/.x`, `?agents=..,.y`) or a stale persisted selection would make
an innocent user's own browser send them. So the frontend drops such ids at
the places they enter: the profile route param, the config store's active
agent (setter and rehydrate), and the bundle-export quick-launch query.

## Design decisions

- The rule mirrors the backend's path-safety check, not an id *format*:
  legacy ids with spaces or unusual characters remain valid.
- `%` is refused too. No real id contains it, and a second decoding layer is
  how `..` is smuggled past a single decode (`%252E%252E`).
- `agentIdFromRoute` returns `''` for malformed escapes instead of throwing;
  the profile page renders an empty id as "missing agent" and sends nothing.

## Upstream / downstream

Used by `stores/configStore.ts`, `pages/AgentProfilePage.tsx`,
`pages/BundleExportPage.tsx`. Backend counterpart: `safe_id_segment`; edge
counterpart: the sentinel's `BoundaryViolationRule` (deploy repo).
