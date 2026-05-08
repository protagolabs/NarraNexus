---
code_file: frontend/src/components/artifacts/ArtifactPreviewCard.tsx
last_verified: 2026-05-08
stub: false
---

# ArtifactPreviewCard.tsx — Inline chat thumbnail for artifact references

## Why it exists

When the agent emits a tool result that creates or references an artifact, the chat thread needs a compact, tappable summary so the user knows what was produced without having to navigate away. This card bridges the chat column and the artifact column: it renders a preview in-place and, on click, focuses the ArtifactColumn on the matching tab.

## Upstream / Downstream

- **Rendered by**: `MessageBubble` (or a future ToolResultBlock) when a tool result payload includes an `artifact_id`.
- **Reads from**: `artifactStore` (for `setActive` / `setCollapsed`). Also makes a direct `fetch()` call to `rawUrl()` for csv/markdown thumbnails.
- **Activates**: `ArtifactColumn` indirectly, via `setCollapsed(false)` + `setActive(artifact_id)`.

## Thumbnail strategy

| Kind | What is shown | Why |
|------|--------------|-----|
| `image/png` / `image/jpeg` | `<img>` pointing at rawUrl | Native browser image decode, no extra fetch needed beyond the img request itself |
| `text/csv` | First 5 rows × first 5 columns via `fetch().then(text)` | Gives at-a-glance data preview; full render needs CsvRenderer |
| `text/markdown` | First 200 chars of the raw text | Fast, avoids pulling in a Markdown renderer just for a thumbnail |
| `application/vnd.echarts+json` | Placeholder label | ECharts requires a full canvas + JS bundle; pointless to partially instantiate in a thumbnail |
| `text/html` | Placeholder label | Same sandboxing complexity as full HtmlRenderer; a thumbnail iframe adds no value |
| `application/pdf` | Placeholder label | Browser PDF viewer must be full-size; thumbnail is not meaningful |

## Design decisions

**Eager fetch for csv/markdown only**: The `useEffect` fires only for the two kinds where a plain text fetch is sufficient to produce a useful thumbnail. Image kinds use a plain `<img>` tag (the browser manages the request). All other kinds skip network activity entirely.

**Click handler order**: `setCollapsed(false)` must be called before `setActive()`. If the column is collapsed and `setActive` fires first, the tab strip might re-render into a collapsed state that is immediately replaced — the ordering avoids a flash.

**No `rounded` corners**: The rest of the UI uses sharp right-angle rectangles (Nordic archive aesthetic). The card uses `border border-[var(--border-default)]` with no border-radius to match.

## Gotchas

The `useEffect` dependency array includes the full `artifact` object. If the parent re-renders with a new `artifact` reference (even with the same data) the effect re-fires and issues a new fetch. Callers should stabilise the prop via `useMemo` or ensure the artifact reference only changes when the content actually changes.
