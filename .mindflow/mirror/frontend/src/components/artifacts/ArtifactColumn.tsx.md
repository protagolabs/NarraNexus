---
code_file: frontend/src/components/artifacts/ArtifactColumn.tsx
last_verified: 2026-05-13
stub: false
---

## 2026-05-13 — Always-visible sliver + auto-expand on new artifact

行为变化：之前 `artifacts.length === 0` 直接 `return null` 整个 unmount，
第一条 artifact 落地时面板"凭空冒出来"——视觉突兀、用户没有心理预期
也来不及反应。

改成：

1. **永远渲染**（去掉 length-0 的 early-return），0 件 artifact 时
   走极简的 sliver 形态——9px 宽的竖条带 "Artifacts" 标签 + chevron。
   用户从一开始就知道这一列存在、artifact 会出现在这里。
2. **新 artifact 到达自动展开**：useRef 跟踪上一帧长度，length
   增长且当前 collapsed 时调 `setCollapsed(false)`。mount 时
   prev=current 保证不会因为 stale-while-revalidate 缓存回填触发
   误展开——只在真正"长出新条目"时弹开。
3. **空态 sliver label** 不显示 `(0)`——纯 "Artifacts" + tooltip
   "Artifacts will appear here once the agent creates one"。

边界 case 取舍：用户手动 collapse 后又有新 artifact → 再次自动
展开。这是用户在 2026-05-13 review 时明确要的语义（"新增就自动
展开一下"）；如果实际用着烦再加 throttle / "only-on-first-growth"
开关。

`effectiveCollapsed = collapsed || artifacts.length === 0`：length 为
0 时强制 sliver，长度 ≥1 时尊重用户的 collapsed 偏好（auto-expand
useEffect 在 0→1 那一刻顺手把这个偏好翻成 false）。

# ArtifactColumn.tsx — 4th layout column for artifact rendering

## Why it exists

The agent layout has three primary columns (nav, chat, context). When the agent produces artifacts, a 4th column slides in to host the full-fidelity renderer. `ArtifactColumn` is the container that owns this column: it wires `ArtifactTabStrip` to the renderer dispatch table and manages the collapsed/expanded state that controls whether the 4th column occupies real estate at all.

## Upstream / Downstream

- **Placed by**: the main agent page layout, side-by-side with the chat panel.
- **Reads from**: `artifactStore` — `artifacts[]`, `activeArtifactId`, `collapsed`, `setCollapsed`.
- **Renders**: `ArtifactTabStrip` + a lazy renderer selected by `RENDERER_BY_KIND`.
- **Lazy imports**: `HtmlRenderer`, `ChartRenderer`, `CsvRenderer`, `ImageRenderer`, `MarkdownRenderer`, `PdfRenderer` — all via `React.lazy`.

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
| `application/pdf` | PdfRenderer | PDF via `<object>` — see PdfRenderer.tsx |

**PDF uses a dedicated PdfRenderer** (C4, 2026-05-09): PDF rendering was separated from HtmlRenderer because the `sandbox="allow-scripts"` iframe approach breaks Firefox's PDF.js renderer (which requires same-origin XHR to load its own worker) and is inconsistent in WKWebView. `PdfRenderer` uses `<object data type="application/pdf">` instead, letting each browser pick its native viewer. The lazy import is added to `ArtifactColumn`'s lazy registry alongside the other renderers.

## Lazy loading rationale

Each renderer is behind `React.lazy`. ECharts is ~500 KB minified; pulling it in on first load would bloat the initial bundle for users who never open a chart artifact. The same logic applies to the Markdown renderer (marked/remark) and the CSV renderer (any virtualised table library). The `<Suspense>` fallback shows a plain loading message while the chunk downloads.

## Collapsed state

When `collapsed === true` and `artifacts.length > 0`, the column renders as an 8px-wide `<button>` sliver at the right edge of the layout. This allows the chat panel to reclaim horizontal space without destroying the artifact state. The collapsed state is persisted to `localStorage` via the store so it survives page refreshes.

When `artifacts.length === 0`, the component returns `null` — the 4th column is entirely absent from the DOM and does not consume layout space.

## Gotchas

**`[writing-mode:vertical-rl]` Tailwind arbitrary property**: The collapsed sliver button uses the Tailwind arbitrary-property syntax `[writing-mode:vertical-rl]` for vertical text. This is valid in any Tailwind v3+ project without any config additions. Do not use a custom utility class like `writing-mode-vertical` — it requires explicit `tailwind.config.js` registration and will be silently no-op if absent.

**Tab strip border**: `ArtifactTabStrip` renders `border-b` on its own outer container. The wrapper `<div>` in `ArtifactColumn` that holds the strip and the collapse button intentionally has **no** `border-b`. Adding it would produce a doubled hairline at the same pixel row.
