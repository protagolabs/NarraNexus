---
code_file: frontend/src/hooks/useTheme.ts
last_verified: 2026-04-24
stub: false
---

# useTheme.ts — Thin selector wrapper over useThemeStore

## Why it exists

Keeps the familiar `{ theme, effectiveTheme, setTheme, toggleTheme, isDark }` shape used across 5+ call sites while sourcing state from the shared Zustand store. The derived `isDark` boolean is the main value-add — components choosing between light/dark asset variants (e.g. `logo-light.png` vs `logo-dark.png`) read it directly.

## Upstream / Downstream

- **Upstream**: `@/stores/themeStore` holds the actual state.
- **Downstream**: `App.tsx`, `ThemeToggle`, `Sidebar`, `LoginPage`, `RegisterPage`, `ModeSelectPage`.

## Design decisions

**Zustand-backed, not useState.** An earlier implementation used `useState` inside this hook. Every call site got its own independent state copy — the toggle updated the toggle's state but Sidebar/Login/Register/ModeSelect instances stayed frozen, so `isDark`-driven image swaps broke after the first toggle. Moving state into `useThemeStore` makes all subscribers re-render on change.

**Hook kept (not replaced by direct store use).** Call sites could import `useThemeStore` directly, but keeping `useTheme` as a one-file wrapper centralizes the `isDark` derivation and preserves a stable surface if the store internals change.

## Gotchas

**DOM side effect lives in App.tsx.** This hook does NOT toggle `document.documentElement.classList`. `App.tsx` owns that `useEffect`, driven by `effectiveTheme` from the store. Do not re-add DOM mutations here — one owner avoids double-apply races.
