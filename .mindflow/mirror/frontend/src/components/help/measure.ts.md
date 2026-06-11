---
code_file: frontend/src/components/help/measure.ts
last_verified: 2026-06-11
stub: false
---

## 2026-06-11 (PM)

Gained `layoutAnnotations` — the pure rail-stacking placement (left/
right note columns + top mode), keeping notes clear of the bottom-
center controls. Estimated note height accounts for the detail line.



# measure.ts — Anchor measurement for the help overlay

Pure function `measureAnnotations(manifest)`: querySelector by
`data-help-id`, skip missing / zero-size / fully-offscreen anchors,
sort by priority. Separate module so tests can import it without
breaking react-refresh's only-export-components rule on the component
file. Used by [[HelpOverlay]].
