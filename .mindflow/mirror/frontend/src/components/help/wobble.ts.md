---
code_file: frontend/src/components/help/wobble.ts
last_verified: 2026-06-11
stub: false
---

# wobble.ts — Hand-drawn SVG stroke generators

## 为什么存在

The overlay needs rough.js-style strokes (arrow / line / open ellipse)
without taking a dependency for ~80 lines of math (spec §12.4 chose
self-written, zero-dep).

## 设计决策

Jitter is **deterministic** — seeded from the input coordinates via a
sin-hash — so identical geometry always yields identical paths and
re-renders never make the strokes "swim". `Math.random()` would also
break any future snapshot testing.
