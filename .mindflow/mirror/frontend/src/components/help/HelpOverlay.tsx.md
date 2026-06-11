---
code_file: frontend/src/components/help/HelpOverlay.tsx
last_verified: 2026-06-11
stub: false
---

# HelpOverlay.tsx — Hand-annotated page guide

## 2026-06-11 (PM) — multi-page overlay + rail layout + centered controls

Owner revision: 3 pages (Agent Setup / Interacting / Teams & Bundles)
switched by handwriting tabs UNDER a bottom-centered "got it" (was
top-right). Notes now live in left/right RAILS — stacked per rail
sorted by anchor Y with collision push-down ([[measure]]
layoutAnnotations), so notes can never overlap at any window size
(the "左侧混乱" fix). Notes gained an optional `detail` second line
for richer explanations.

## 2026-06-11 — theme-stable annotation ink

First deploy was blank in dark mode: strokes/notes used --nm-paper,
which flips to near-black in .dark while the backdrop is dark in BOTH
themes. Ink now uses --color-gray-50 (a fixed @theme constant) and the
backdrop has an explicit dark fallback. Lesson: anything painted on
the dim layer must use theme-STABLE light tones, never --nm-* tokens.

## 为什么存在

Owner requirement (spec §12): "界面太复杂" — a bottom-left ? opens a
dimmed overlay where handwritten notes + wobbly arrows explain the live
controls. It extends the NM paper motif: the overlay is *another hand
writing on the paper*. It complements OnboardingChecklist: checklist =
"what to do first" (task), overlay = "what is this" (map).

## 设计决策

- **Anchor registry, never static art** (the load-bearing decision):
  controls carry `data-help-id`; [[measure]] reads
  getBoundingClientRect at open time and SKIPS missing/invisible/
  offscreen anchors — layout evolution can never leave an arrow
  pointing at air. Annotation manifests ([[helpContent]]) are pure
  data, decoupled from UI code.
- Measurement is a render-time derivation (useMemo + resize tick), not
  setState-in-effect — React Compiler lint forbids the latter.
- Notes are real DOM text (screen-reader readable); only the look is
  handwritten. Strokes live in a separate pointer-events-none SVG.
- Stagger entrance (60ms/note) = "written one by one".
- Empty manifest renders an explicit fallback line, never a blank dim.

## 新人易踩的坑

aria-modal without a focus trap is a known v1 gap; the only focusable
element inside is the close button.
