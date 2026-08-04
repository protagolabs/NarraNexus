---
code_file: frontend/src/components/artifacts/renderers/CsvRenderer.tsx
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — 滚动归属：wrapper 补 h-full，纵横都在这一层滚

同 MarkdownRenderer 的滑不动修复（Base recvpm05jsLg3o）：wrapper div 补
`h-full` + `overscroll-contain`。列容器内容盒 overflow-hidden 不能动
（拖拽冻结依赖裁剪），高度钉死后纵向滚动和宽表横向滚动都发生在这个
wrapper 的 `overflow-auto` 里——正好延续下面「overflow-auto 在 wrapper
不在 table 上」的既有决策。回归测试见 [[__tests__/scrollContainment.test.tsx]]。

## 2026-05-27 — same Dismiss-loop fix as HtmlRenderer

`heal.attempt` now via `attemptRef`; load effect deps reduced to
`[url]`. See HtmlRenderer.tsx.md + useArtifactHeal.ts.md for the
shared bug story (modal stuck open after Dismiss, P0 2026-05-25).

## 2026-05-14 — drop `version` prop, fetch via `useArtifactRawUrl`

Renderer no longer takes a `version` prop. Uses `useArtifactRawUrl` for the
token-protected public URL; `fetchArtifactText` no longer adds an Authorization
header (raw is on the JWT-bypassed `/api/public/artifacts/...` prefix and the
HMAC token in the path is the auth).

# CsvRenderer.tsx — Tabular renderer for text/csv artifacts

## Why it exists

Fetches agent-generated CSV and renders it as a scrollable HTML `<table>` so users can inspect tabular data inline without downloading the file. Agent-generated CSVs are typically small (a few hundred rows at most), so a simple all-in-memory approach is fine.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` via `React.lazy`, dispatched when `artifact.kind === 'text/csv'`.
- **Calls**: `rawUrl()` from `@/types/artifact` for the fetch target.

## Design decisions

**Naive comma-split parser (`parseCsv`).** Does not handle RFC 4180 quoted fields (e.g., `"hello, world",next`). Agent-generated CSVs that need the full spec should use a proper parser like `papaparse`. The parser is isolated in a pure function at the top of the file, so swapping it out requires changing exactly one line without touching the component.

**First row treated as header unconditionally.** There is no heuristic to detect whether a header row exists. Agents that emit headerless CSVs should add a header row. This is a conscious trade-off: guessing is error-prone and the agent is the authority on its own output format.

**`overflow-auto` on the wrapper, not the table.** The table uses `border-collapse` which can conflict with `overflow` clipping on the table element itself. Wrapping in a `<div>` with `overflow-auto` avoids that CSS quirk.

## Gotchas

Very large CSVs (thousands of rows) will render slowly and occupy a lot of DOM nodes. No pagination or virtualisation is implemented. This is acceptable for agent-emitted tabular results; production data import pipelines need a different component.

**Empty CSV guard (I6, 2026-05-09)**: An explicit `rows.length === 0` check was added before the `const [header, ...body] = rows` destructuring. Without it, an agent that emits a zero-byte or whitespace-only CSV file would produce `rows = []` after `parseCsv()`, and `header.map(...)` would throw `TypeError: Cannot read properties of undefined`. Now an empty CSV renders a `"(Empty CSV)"` placeholder instead of crashing.
