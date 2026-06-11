/**
 * @file_name: measure.ts
 * @author:
 * @date: 2026-06-11
 * @description: Anchor measurement for the help overlay. Lives outside the
 * component file so it can be exported for tests without breaking the
 * react-refresh only-export-components rule.
 */

import type { HelpAnnotation } from './helpContent';

export interface MeasuredAnnotation extends HelpAnnotation {
  rect: { x: number; y: number; width: number; height: number };
}

/**
 * Measure every annotation's anchor. Exported for tests.
 * Skips: missing anchors, zero-size rects, rects fully outside viewport.
 */
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

