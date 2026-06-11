/**
 * @file_name: wobble.ts
 * @author:
 * @date: 2026-06-11
 * @description: Tiny hand-drawn SVG path generators for the help overlay.
 *
 * Zero-dependency alternative to rough.js: each generator returns an SVG
 * path `d` string with slight, DETERMINISTIC jitter (seeded by the input
 * coordinates) so strokes look hand-drawn but never change between
 * renders of the same geometry.
 */

/** Deterministic pseudo-random in [-1, 1) seeded by three numbers. */
function jitter(seed1: number, seed2: number, seed3: number): number {
  const x = Math.sin(seed1 * 127.1 + seed2 * 311.7 + seed3 * 74.7) * 43758.5453;
  return (x - Math.floor(x)) * 2 - 1;
}

export interface Point {
  x: number;
  y: number;
}

/**
 * A wobbly line from `from` to `to` drawn as two slightly-off quadratic
 * segments — the way a quick pen stroke bows around its midpoint.
 */
export function wobblyLine(from: Point, to: Point, amplitude = 3): string {
  const midX = (from.x + to.x) / 2;
  const midY = (from.y + to.y) / 2;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.max(1, Math.hypot(dx, dy));
  // Unit normal — the bow direction.
  const nx = -dy / len;
  const ny = dx / len;
  const bow = amplitude * (1 + Math.abs(jitter(from.x, to.y, len)));
  const c1x = midX + nx * bow + jitter(from.x, from.y, 1) * amplitude;
  const c1y = midY + ny * bow + jitter(from.y, to.x, 2) * amplitude;
  return `M ${from.x} ${from.y} Q ${c1x} ${c1y} ${to.x} ${to.y}`;
}

/**
 * A wobbly arrow: line + two short head strokes at the `to` end.
 */
export function wobblyArrow(from: Point, to: Point, headLen = 9): string {
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const spread = 0.46 + Math.abs(jitter(to.x, to.y, 3)) * 0.12;
  const h1: Point = {
    x: to.x - headLen * Math.cos(angle - spread),
    y: to.y - headLen * Math.sin(angle - spread),
  };
  const h2: Point = {
    x: to.x - headLen * Math.cos(angle + spread),
    y: to.y - headLen * Math.sin(angle + spread),
  };
  return [
    wobblyLine(from, to),
    wobblyLine(h1, to, 1),
    wobblyLine(h2, to, 1),
  ].join(' ');
}

/**
 * A hand-drawn ellipse around a rect (the "circle this button" stroke).
 * Slightly overshoots the rect and doesn't quite close — like a real
 * pen circle.
 */
export function wobblyEllipse(
  cx: number,
  cy: number,
  rx: number,
  ry: number,
): string {
  const segments = 8;
  const overshoot = 1.06; // ~382° — pen circles overlap their start
  const start = jitter(cx, cy, 4) * 0.8;
  let d = '';
  for (let i = 0; i <= segments; i++) {
    const t = start + (i / segments) * Math.PI * 2 * overshoot;
    const wx = jitter(cx + i, cy, 5) * (rx * 0.05);
    const wy = jitter(cx, cy + i, 6) * (ry * 0.05);
    const px = cx + Math.cos(t) * (rx + wx);
    const py = cy + Math.sin(t) * (ry + wy);
    d += i === 0 ? `M ${px} ${py}` : ` L ${px} ${py}`;
  }
  return d;
}
