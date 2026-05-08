---
code_file: frontend/src/components/artifacts/renderers/MarkdownRenderer.tsx
last_verified: 2026-05-08
stub: false
---

# MarkdownRenderer.tsx — Markdown artifact renderer

## Why it exists

Fetches a `text/markdown` artifact and renders it with ReactMarkdown + remark-gfm. Artifact Markdown is distinct from chat-bubble Markdown (`ui/Markdown.tsx`) in that the content arrives from a fetch rather than a prop string — hence a separate component.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` via `React.lazy`, dispatched when `artifact.kind === 'text/markdown'`.
- **Calls**: `rawUrl()` from `@/types/artifact` for the fetch target.
- **Bundle**: Both this file and `ui/Markdown.tsx` import from `react-markdown` and `remark-gfm`, which land in the `vendor-markdown` manualChunk. No additional bundle cost once the chunk is loaded.

## Design decisions

**`prose prose-invert`** — uses Tailwind Typography plugin classes for readable body text in dark mode. The artifact tab pane background is dark, so `prose-invert` flips the typography tokens to light text.

**No rehype-raw.** Unlike `ui/Markdown.tsx` (which enables raw HTML for message bubbles that may contain agent-formatted HTML fragments), the artifact renderer intentionally omits `rehypeRaw` so that literal HTML tags in the Markdown are escaped rather than rendered. An agent that needs rendered HTML should emit a `text/html` artifact rendered by `HtmlRenderer` instead.

**Empty string initial state.** `setText` fires on each `version` change. Starting from `''` means the component renders a blank `<div>` during the fetch rather than stale content from the previous version. Acceptable flicker for the typical small-to-medium Markdown files agents emit.

## Gotchas

The `useEffect` fetch has no abort controller. If `version` changes quickly (e.g., the user flips through version history), multiple concurrent fetches may race. The last one to resolve wins, which is usually correct (monotonically increasing versions). For a more rigorous fix, add `AbortController` inside the effect — low priority given typical usage patterns.
