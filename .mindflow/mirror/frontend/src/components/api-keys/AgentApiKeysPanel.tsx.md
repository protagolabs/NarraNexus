---
code_file: frontend/src/components/api-keys/AgentApiKeysPanel.tsx
last_verified: 2026-06-12
stub: false
---

## 2026-06-12 — fix transparent modal

The plaintext-reveal modal originally used `bg-card` + `border-border`
+ `bg-muted` + `text-muted-foreground` + `bg-destructive` —
shadcn-style tokens that are NOT defined in this project's Tailwind v4
`@theme` block. CSS variables resolved to nothing, the modal card was
literally transparent, and the panel's token list bled through it
(reported by Bin哥 with a screenshot of "Token rotated" overlapping
the existing token rows behind).

Fix: the inline `<div className="fixed inset-0 z-50 ...">` modal was
replaced with the canonical `<Dialog>` primitive
([[../ui/Dialog.tsx]]). Dialog `createPortal`s into `document.body`,
ships an opaque NM-raised background, sits at `z-[1000]/[1001]`, and
locks body scroll while open — so no parent transform or stacking
context can interfere.

In the same sweep the other shadcn-style tokens scattered across this
file (error banner, input border, list row borders, muted text, code
block background) were rewritten to use NM CSS variables directly:
- `var(--text-primary)` / `var(--text-tertiary)` for text
- `var(--border-default)` / `var(--border-subtle)` for hairlines
- `var(--bg-primary)` / `var(--bg-elevated)` for surfaces
- `color-mix(in srgb, var(--color-error) 10%, transparent)` for the
  error/warning/success tinted backgrounds (keeps the soft-tint
  effect that `bg-destructive/10` was supposed to deliver)

Same `StatusBadge` token rewrite: active=success, expired=warning,
revoked=error, each tinted via `color-mix`.

# AgentApiKeysPanel.tsx — owner-facing nxk_ token management

## 为什么存在

Step 9 of the external API protocol (v0.3). Owners of an agent need
to mint, rotate, and revoke `nxk_` tokens so external integrators
(Arena 客服, future Manyfold clients) can call `/v1/external/*` on
that agent. This panel is the only frontend surface for managing
those tokens — the plaintext is shown once at create / rotate time,
SHA256 is what lives in the DB.

## 上下游关系

- **被谁用**: [[../bookmarks/BookmarkPanelHost.tsx]] (lazy-loaded when
  the `api-keys` bookmark tab is active on an agent detail page).
- **依赖谁**: `api.listAgentApiKeys` / `createAgentApiKey` /
  `rotateAgentApiKey` / `revokeAgentApiKey` (in `@/lib/api`) which
  hit `backend/routes/agent_api_keys.py`; [[../ui/Dialog.tsx]] for
  the plaintext-reveal modal; `Button` for actions.

## 设计决策

**One-time plaintext reveal**. After the user closes the Dialog the
token is gone for good. The close handler runs a `confirm()` ("Have
you copied and stored the token?") so an accidental ESC or
click-outside doesn't burn a freshly-minted token. The Dialog itself
plumbs both ESC and backdrop-click through the same `onClose` so the
guard runs uniformly.

**Default scopes** `['chat', 'session.delete', 'session.list']` are
hard-coded at create time — the UI doesn't currently expose
per-scope choice. Owners who need a narrower token use the
management API (`POST /api/agents/{id}/api-keys`) directly with a
custom `scopes` field.

**Rotate before revoke**. Rotate keeps an audit trail and gives the
integrator a 7-day grace window on the old token; revoke is
immediate-401. UI surfaces both as separate buttons, with Rotate
preferred (Revoke deliberately styled with `var(--color-error)` to
signal "this is the nuclear option").

## Gotcha / 边界情况

**Never reintroduce shadcn tokens** in this file. This project uses
Tailwind v4 with a custom `@theme` that defines `--color-*` /
`--font-*` / `--radius-*` only. `bg-card`, `bg-muted`,
`text-muted-foreground`, `bg-destructive`, `border-border`,
`border-input`, `bg-background`, `bg-popover` are all undefined →
they compile to `var(--color-<undefined>)` → render transparent. If
you need a "muted background" use `var(--bg-elevated)`; for borders
use `var(--border-default)` or `var(--border-subtle)`; for tinted
status colors use `color-mix(in srgb, var(--color-error) 10%,
transparent)`.

**Status badge colors** are inlined via `style={{...}}` rather than
via Tailwind arbitrary values, because `color-mix(...)` inside a
Tailwind class string is awkward (escaping) and breaks editor
autocomplete. Inline is cleaner here.

**The Dialog renders into `document.body`** via `createPortal`. That
means the modal sits OUTSIDE the bookmark drawer's overflow-y-auto
container and OUTSIDE every transform: ancestor — so it cannot be
clipped or pushed into the wrong stacking context. This is the
reason the visible bug went away.
