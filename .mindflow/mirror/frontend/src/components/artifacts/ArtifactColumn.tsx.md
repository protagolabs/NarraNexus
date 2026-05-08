---
code_file: frontend/src/components/artifacts/ArtifactColumn.tsx
last_verified: 2026-05-08
stub: false
---

# ArtifactColumn.tsx — 4th layout column for artifact rendering

## Why it exists

The agent layout has three primary columns (nav, chat, context). When the agent produces artifacts, a 4th column slides in to host the full-fidelity renderer. `ArtifactColumn` is the container that owns this column: it wires `ArtifactTabStrip` to the renderer dispatch table and manages the collapsed/expanded state that controls whether the 4th column occupies real estate at all.

## Upstream / Downstream

- **Placed by**: the main agent page layout, side-by-side with the chat panel.
- **Reads from**: `artifactStore` — `artifacts[]`, `activeArtifactId`, `collapsed`, `setCollapsed`.
- **Renders**: `ArtifactTabStrip` + a lazy renderer selected by `RENDERER_BY_KIND`.
- **Lazy imports**: `HtmlRenderer`, `ChartRenderer`, `CsvRenderer`, `ImageRenderer`, `MarkdownRenderer` — all via `React.lazy`.

## Renderer dispatch

`RENDERER_BY_KIND` is a `Record<ArtifactKind, RendererComponent>` lookup that maps each MIME kind to a lazy-loaded renderer. All seven `ArtifactKind` values are covered:

| Kind | Renderer | Notes |
|------|----------|-------|
| `text/html` | HtmlRenderer | Sandboxed iframe |
| `application/vnd.echarts+json` | ChartRenderer | ECharts canvas |
| `text/csv` | CsvRenderer | Virtualised table |
| `text/markdown` | MarkdownRenderer | Markdown → HTML |
| `image/png` | ImageRenderer | `<img>` with zoom |
| `image/jpeg` | ImageRenderer | Same as png |
| `application/pdf` | HtmlRenderer | PDF via browser native viewer in iframe |

**PDF reuses HtmlRenderer**: the `/raw` endpoint serves PDF bytes with `Content-Type: application/pdf`. When an `<iframe>` loads this URL, the browser invokes its native PDF viewer inside the iframe. This gives PDF the same CSP + null-origin sandbox isolation as HTML artifacts at zero extra implementation. The security properties are identical: `sandbox="allow-scripts"` without `allow-same-origin` means the PDF viewer JS (if any) is null-origin isolated.

## Lazy loading rationale

Each renderer is behind `React.lazy`. ECharts is ~500 KB minified; pulling it in on first load would bloat the initial bundle for users who never open a chart artifact. The same logic applies to the Markdown renderer (marked/remark) and the CSV renderer (any virtualised table library). The `<Suspense>` fallback shows a plain loading message while the chunk downloads.

## Collapsed state

When `collapsed === true` and `artifacts.length > 0`, the column renders as an 8px-wide `<button>` sliver at the right edge of the layout. This allows the chat panel to reclaim horizontal space without destroying the artifact state. The collapsed state is persisted to `localStorage` via the store so it survives page refreshes.

When `artifacts.length === 0`, the component returns `null` — the 4th column is entirely absent from the DOM and does not consume layout space.

## Gotchas

**`writing-mode-vertical` Tailwind class**: The collapsed sliver button uses this class for vertical text. This is a custom utility that must exist in `tailwind.config.js` (or be inlined as an arbitrary CSS property). If the class is absent, the button text will render horizontally and overflow the 8px width. Consider replacing with `[writing-mode:vertical-rl]` if the custom class is not configured.

**Tab strip border-b conflict**: `ArtifactTabStrip` already renders `border-b` on its own container. The `<div>` wrapping the strip and collapse button in `ArtifactColumn` also has `border-b`. This means two hairlines stack. If the visual result is a double border, remove the `border-b` from the wrapper div in `ArtifactColumn` and rely solely on the strip's own border.
