---
code_file: frontend/src/components/help/measure.ts
last_verified: 2026-06-11
stub: false
---

## 2026-06-11 (round 4) — fixed rails + nearest-border arrows

Two layout bugs from Owner review with the artifact column expanded:
the right rail's x was derived from target extents, so the artifact
column (a huge rect reaching mid-screen) dragged the rail across the
page and arrows to the strip stretched the full width. Rails now sit
at FIXED screen-edge offsets, and arrows aim at the nearest point on
the target's BORDER (clamp-to-rect, with an inside-rect escape through
the nearer vertical edge), backed off 8px. Arrow origin = the
headline's vertical center on the side facing the target; PlacedAnnotation
gained `align` so the note text justifies toward its arrow.

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
