/**
 * @file_name: measure.ts
 * @author:
 * @date: 2026-06-11
 * @description: Anchor measurement + rail layout for the help overlay.
 *
 * Measurement: querySelector by data-help-id, skip missing / zero-size /
 * fully-offscreen anchors, sort by priority.
 *
 * Layout (round-4 rules):
 *  - Rails sit at FIXED screen-edge offsets — never derived from target
 *    extents, so a huge anchor (artifact column, conversation area)
 *    cannot drag a rail across the screen.
 *  - Arrows aim at the point on the target's BORDER nearest the note
 *    (then back off a few px), so wide targets get short edge-touching
 *    arrows instead of screen-crossing ones.
 *  - The arrow leaves the note at its headline's vertical center, on
 *    the side facing the target; note text aligns toward that side so
 *    text and arrow read as one gesture.
 */

import type { HelpAnnotation } from './helpContent';

export interface MeasuredAnnotation extends HelpAnnotation {
  rect: { x: number; y: number; width: number; height: number };
}

export interface PlacedAnnotation extends MeasuredAnnotation {
  noteX: number;
  noteY: number;
  noteW: number;
  /** Text alignment toward the arrow side. */
  align: 'left' | 'right' | 'center';
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

const NOTE_W = 310;
/** Estimated note height: headline + optional detail (wrapped).
 *  Metrics track HelpOverlay's type scale (26px note / 19px detail). */
function estimateHeight(a: MeasuredAnnotation): number {
  const headline = 38;
  if (!a.detail) return headline + 12;
  const lines = Math.ceil(a.detail.length / 30);
  return headline + lines * 24 + 12;
}

/** Nearest point on a rect's border to `p`, backed off by `inset` px. */
function nearestBorderPoint(
  p: { x: number; y: number },
  rect: { x: number; y: number; width: number; height: number },
  inset: number,
): { x: number; y: number } {
  let bx = Math.max(rect.x, Math.min(p.x, rect.x + rect.width));
  let by = Math.max(rect.y, Math.min(p.y, rect.y + rect.height));
  const inside =
    p.x > rect.x && p.x < rect.x + rect.width &&
    p.y > rect.y && p.y < rect.y + rect.height;
  if (inside) {
    // Note overlaps a huge target — exit through the nearer vertical edge.
    const toLeft = p.x - rect.x;
    const toRight = rect.x + rect.width - p.x;
    bx = toLeft < toRight ? rect.x : rect.x + rect.width;
    by = p.y;
  }
  // Back off along the border→note direction so the tip floats just
  // outside the control.
  const dx = p.x - bx;
  const dy = p.y - by;
  const len = Math.max(1, Math.hypot(dx, dy));
  return { x: bx + (dx / len) * inset, y: by + (dy / len) * inset };
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
  const GAP = 22;
  const FOOTER = 140; // keep clear of the centered controls
  const HEADER = 84;  // keep clear of the top-center page title

  // Fixed rail columns (clamped for narrow windows).
  const railX: Record<'left' | 'right', number> = {
    left: Math.min(Math.max(vw * 0.24, 300), vw * 0.42),
    right: Math.max(vw - NOTE_W - 96, vw * 0.5),
  };

  for (const rail of ['left', 'right'] as const) {
    const items = measured
      .filter((m) => m.rail === rail)
      .sort((a, b) => a.rect.y - b.rect.y);
    const noteX = railX[rail];

    let cursorY = HEADER;
    for (const m of items) {
      const h = estimateHeight(m);
      const targetY = m.rect.y + m.rect.height / 2;
      const noteY = Math.min(
        Math.max(cursorY, targetY - h / 2),
        vh - FOOTER - h,
      );
      cursorY = noteY + h + GAP;

      // Arrow leaves at the headline's vertical center, on the side
      // facing the target; text aligns to that side.
      const headlineMidY = noteY + 19;
      const from = {
        x: rail === 'left' ? noteX - 8 : noteX + NOTE_W + 8,
        y: headlineMidY,
      };
      const to = nearestBorderPoint(from, m.rect, 8);
      placed.push({
        ...m,
        noteX,
        noteY,
        noteW: NOTE_W,
        align: rail === 'left' ? 'left' : 'right',
        from,
        to,
      });
    }
  }

  // 'top' rail: note centered above its anchor (used sparingly — composer).
  for (const m of measured.filter((mm) => mm.rail === 'top')) {
    const h = estimateHeight(m);
    const cx = m.rect.x + m.rect.width / 2;
    const noteX = Math.max(16, Math.min(cx - NOTE_W / 2, vw - NOTE_W - 16));
    const noteY = Math.max(HEADER, m.rect.y - 90 - h);
    const from = { x: cx, y: noteY + h };
    placed.push({
      ...m,
      noteX,
      noteY,
      noteW: NOTE_W,
      align: 'center',
      from,
      to: nearestBorderPoint(from, m.rect, 8),
    });
  }

  return placed;
}
