/**
 * @file_name: measure.ts
 * @author:
 * @date: 2026-06-11
 * @description: Anchor measurement + rail layout for the help overlay.
 *
 * Measurement: querySelector by data-help-id, skip missing / zero-size /
 * fully-offscreen anchors, sort by priority.
 *
 * Layout: notes are stacked per rail (left / right vertical columns,
 * top = above its anchor) sorted by anchor Y, each pushed below the
 * previous one — notes can NEVER overlap, whatever the window size.
 */

import type { HelpAnnotation } from './helpContent';

export interface MeasuredAnnotation extends HelpAnnotation {
  rect: { x: number; y: number; width: number; height: number };
}

export interface PlacedAnnotation extends MeasuredAnnotation {
  noteX: number;
  noteY: number;
  noteW: number;
  from: { x: number; y: number };
  to: { x: number; y: number };
}

export function measureAnnotations(
  annotations: HelpAnnotation[],
): MeasuredAnnotation[] {
  const out: MeasuredAnnotation[] = [];
  for (const a of annotations) {
    const el = document.querySelector(`[data-help-id="${a.helpId}"]`);
    if (!el) continue;
    const r = (el as HTMLElement).getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (
      r.right < 0 ||
      r.bottom < 0 ||
      r.left > window.innerWidth ||
      r.top > window.innerHeight
    ) {
      continue;
    }
    out.push({ ...a, rect: { x: r.x, y: r.y, width: r.width, height: r.height } });
  }
  return out.sort((a, b) => a.priority - b.priority);
}

const NOTE_W = 250;
/** Estimated note height: headline + optional detail (wrapped). */
function estimateHeight(a: MeasuredAnnotation): number {
  const headline = 30;
  if (!a.detail) return headline + 10;
  const lines = Math.ceil(a.detail.length / 34);
  return headline + lines * 19 + 10;
}

/**
 * Place every measured annotation. Pure given (annotations, viewport).
 */
export function layoutAnnotations(
  measured: MeasuredAnnotation[],
  vw: number,
  vh: number,
): PlacedAnnotation[] {
  const placed: PlacedAnnotation[] = [];
  const GAP = 14;
  const FOOTER = 120; // keep clear of the centered controls

  for (const rail of ['left', 'right'] as const) {
    const items = measured
      .filter((m) => m.rail === rail)
      .sort((a, b) => a.rect.y - b.rect.y);
    // Rail x: left rail sits just right of the sidebar targets; right
    // rail sits left of the strip/drawer targets.
    const maxRight = Math.max(0, ...items.map((m) => m.rect.x + m.rect.width));
    const minLeft = Math.min(vw, ...items.map((m) => m.rect.x));
    const noteX =
      rail === 'left'
        ? Math.min(maxRight + 70, vw - NOTE_W - 16)
        : Math.max(minLeft - 70 - NOTE_W, 16);

    let cursorY = 16;
    for (const m of items) {
      const h = estimateHeight(m);
      const targetY = m.rect.y + m.rect.height / 2;
      const noteY = Math.min(
        Math.max(cursorY, targetY - h / 2),
        vh - FOOTER - h,
      );
      cursorY = noteY + h + GAP;
      const from = {
        x: rail === 'left' ? noteX - 6 : noteX + NOTE_W + 6,
        y: noteY + 14,
      };
      const to = {
        x: rail === 'left' ? m.rect.x + m.rect.width + 8 : m.rect.x - 8,
        y: targetY,
      };
      placed.push({ ...m, noteX, noteY, noteW: NOTE_W, from, to });
    }
  }

  // 'top' rail: note centered above its anchor (used sparingly — composer).
  for (const m of measured.filter((mm) => mm.rail === 'top')) {
    const h = estimateHeight(m);
    const cx = m.rect.x + m.rect.width / 2;
    const noteX = Math.max(16, Math.min(cx - NOTE_W / 2, vw - NOTE_W - 16));
    const noteY = Math.max(16, m.rect.y - 90 - h);
    placed.push({
      ...m,
      noteX,
      noteY,
      noteW: NOTE_W,
      from: { x: cx, y: noteY + h },
      to: { x: cx, y: m.rect.y - 8 },
    });
  }

  return placed;
}
