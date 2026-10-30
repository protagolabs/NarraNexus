---
code_file: frontend/src/lib/agentIdSafety.ts
last_verified: 2026-10-30
stub: false
---

# agentIdSafety.ts — ids that can never be a real agent never leave the browser

## Why it exists

The backend refuses an agent id that is not exactly one path segment
(`workspace_paths.safe_id_segment`: empty, NUL, `/`, `\`, or dot-led such as
`..`) and logs it as a signed tenant-boundary violation. Since monitoring v2
the edge monitor escalates when one verified account sends two or more
*different* such values within ten minutes: it suspends the account and bans
the IPs only that account uses. That is right for a prober, but a crafted link
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
