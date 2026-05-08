---
code_file: frontend/src/components/artifacts/renderers/ChartRenderer.tsx
last_verified: 2026-05-08
stub: false
---

# ChartRenderer.tsx — ECharts renderer for application/vnd.echarts+json artifacts

## Why it exists

Renders ECharts option JSON emitted by an agent as an interactive chart. ECharts (~700 KB min+gzip) is intentionally loaded via dynamic `import('echarts')` rather than a static import, keeping it out of the initial bundle and out of the `vendor-echarts` manualChunk unless this component actually mounts.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` via `React.lazy`, dispatched when `artifact.kind === 'application/vnd.echarts+json'`.
- **Calls**: `rawUrl()` from `@/types/artifact` for the fetch target, then `echarts.init()` + `c.setOption()` on the resolved `<div>` ref.

## Design decisions

**Dynamic `import('echarts')` inside `useEffect`.** Two-level laziness: React.lazy defers the component itself; `import('echarts')` defers the library until the component actually runs its effect. This means the ~700 KB library is not downloaded even if `React.lazy` pre-fetches the component module.

**`disposed` flag + `chart.dispose()`.** The async IIFE inside `useEffect` has multiple `await` points. If the component unmounts between them, `ref.current` may be null and calling `echarts.init(null)` would throw. The `disposed` flag short-circuits after each `await`. `chart.dispose()` in the cleanup function releases the canvas WebGL context so it can be GC'd and not leak across tab switches.

**No resize observer.** The chart is initialised once at the div's current dimensions. If the panel is resized, the chart will not adapt. A `ResizeObserver` + `chart.resize()` call can be added without changing the contract — defer until a user reports it.

**Agent is responsible for valid option JSON.** No schema validation is performed on the fetched payload. A malformed option silently produces an empty or broken chart; the error boundary (if any) in `ArtifactColumn` will catch thrown exceptions from `c.setOption()`.

## Gotchas

`echarts.init(ref.current)` will fail if `ref.current` has zero dimensions (display:none, width:0). The tab system must ensure the container has non-zero dimensions before the renderer mounts.
