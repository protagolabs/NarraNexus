---
code_file: frontend/src/components/help/measure.ts
last_verified: 2026-06-11
stub: false
---

# measure.ts — Anchor measurement for the help overlay

Pure function `measureAnnotations(manifest)`: querySelector by
`data-help-id`, skip missing / zero-size / fully-offscreen anchors,
sort by priority. Separate module so tests can import it without
breaking react-refresh's only-export-components rule on the component
file. Used by [[HelpOverlay]].
