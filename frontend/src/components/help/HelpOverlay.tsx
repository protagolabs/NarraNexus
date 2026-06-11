/**
 * @file_name: HelpOverlay.tsx
 * @author:
 * @date: 2026-06-11
 * @description: Hand-annotated help overlay — "another hand wrote on the
 * paper". Dims the page and draws handwritten notes + wobbly arrows
 * pointing at live controls.
 *
 * Anchor registry: controls carry `data-help-id`; the overlay measures
 * them with getBoundingClientRect at open time (and on resize) and skips
 * anything missing or invisible — layout evolution can never leave an
 * arrow pointing at air.
 *
 * The handwriting font (Caveat, SIL OFL, 41 KB latin subset bundled at
 * /fonts/caveat-annotations.woff2) is declared via @font-face in
 * index.css — browsers fetch it lazily on first use, i.e. the first
 * time this overlay opens.
 */

import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import type { HelpAnnotation } from './helpContent';
import { measureAnnotations, type MeasuredAnnotation } from './measure';
import { wobblyArrow, wobblyEllipse } from './wobble';

/** Note box placement + arrow endpoints for one annotation. */
function layoutFor(m: MeasuredAnnotation) {
  const { rect, side } = m;
  const cx = rect.x + rect.width / 2;
  const cy = rect.y + rect.height / 2;
  const GAP = 56; // distance from anchor edge to note box
  const NOTE_W = 200;

  let noteX = cx;
  let noteY = cy;
  if (side === 'right') {
    noteX = rect.x + rect.width + GAP;
    noteY = cy - 14;
  } else if (side === 'left') {
    noteX = rect.x - GAP - NOTE_W;
    noteY = cy - 14;
  } else if (side === 'top') {
    noteX = cx - NOTE_W / 2;
    noteY = rect.y - GAP - 28;
  } else {
    noteX = cx - NOTE_W / 2;
    noteY = rect.y + rect.height + GAP;
  }
  // Clamp into viewport.
  noteX = Math.max(8, Math.min(noteX, window.innerWidth - NOTE_W - 8));
  noteY = Math.max(8, Math.min(noteY, window.innerHeight - 60));

  // Arrow: from the note edge nearest the anchor → anchor edge.
  const from = {
    x: side === 'right' ? noteX - 4 : side === 'left' ? noteX + NOTE_W + 4 : cx,
    y: side === 'top' ? noteY + 34 : side === 'bottom' ? noteY - 4 : noteY + 12,
  };
  const to = {
    x: side === 'right' ? rect.x + rect.width + 6 : side === 'left' ? rect.x - 6 : cx,
    y: side === 'top' ? rect.y - 6 : side === 'bottom' ? rect.y + rect.height + 6 : cy,
  };
  return { noteX, noteY, noteW: NOTE_W, from, to };
}

interface HelpOverlayProps {
  open: boolean;
  annotations: HelpAnnotation[];
  onClose: () => void;
}

export function HelpOverlay({ open, annotations, onClose }: HelpOverlayProps) {
  // Re-measure on window resize by bumping a tick; the measurement itself
  // is a pure render-time derivation (DOM reads only, no side effects).
  const [resizeTick, setResizeTick] = useState(0);
  const measured = useMemo<MeasuredAnnotation[]>(
    () => (open ? measureAnnotations(annotations) : []),
    // resizeTick is an intentional extra dep: same inputs, but the DOM
    // geometry it reads has changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open, annotations, resizeTick],
  );

  useEffect(() => {
    if (!open) return;
    const onResize = () => setResizeTick((t) => t + 1);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('resize', onResize);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('resize', onResize);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Page guide"
      className="fixed inset-0 z-[300] animate-fade-in"
      style={{ background: 'var(--nm-backdrop)' }}
      onClick={onClose}
    >
      {/* Stroke layer */}
      <svg
        className="absolute inset-0 w-full h-full pointer-events-none"
        aria-hidden
      >
        {measured.map((m, i) => {
          const { from, to } = layoutFor(m);
          return (
            <g
              key={m.helpId}
              className="help-stroke-in"
              style={{ animationDelay: `${i * 60}ms` }}
            >
              <path
                d={wobblyArrow(from, to)}
                fill="none"
                stroke="var(--nm-paper)"
                strokeWidth={1.8}
                strokeLinecap="round"
              />
              {m.circle && (
                <path
                  d={wobblyEllipse(
                    m.rect.x + m.rect.width / 2,
                    m.rect.y + m.rect.height / 2,
                    m.rect.width / 2 + 9,
                    m.rect.height / 2 + 9,
                  )}
                  fill="none"
                  stroke="var(--nm-paper)"
                  strokeWidth={1.6}
                  strokeLinecap="round"
                />
              )}
            </g>
          );
        })}
      </svg>

      {/* Note layer — real DOM text (screen-reader readable), styled as
          handwriting. */}
      {measured.map((m, i) => {
        const { noteX, noteY, noteW } = layoutFor(m);
        return (
          <div
            key={m.helpId}
            className="absolute help-note-in"
            style={{
              left: noteX,
              top: noteY,
              width: noteW,
              animationDelay: `${i * 60}ms`,
              fontFamily: 'var(--font-handwriting)',
              fontSize: 19,
              lineHeight: 1.25,
              color: 'var(--nm-paper)',
              textShadow: '0 1px 2px rgba(0,0,0,0.35)',
            }}
          >
            {m.note}
          </div>
        );
      })}

      {/* Empty-manifest fallback so the overlay never opens "blank". */}
      {measured.length === 0 && (
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{
            fontFamily: 'var(--font-handwriting)',
            fontSize: 24,
            color: 'var(--nm-paper)',
          }}
        >
          Nothing to explain on this page yet.
        </div>
      )}

      {/* Close affordance */}
      <button
        type="button"
        aria-label="Close guide"
        onClick={onClose}
        className="absolute top-4 right-4 flex items-center gap-2 px-3 py-1.5 rounded-[var(--radius-sm)] cursor-pointer"
        style={{
          fontFamily: 'var(--font-handwriting)',
          fontSize: 18,
          color: 'var(--nm-paper)',
          border: '1px solid rgba(255,255,255,0.4)',
        }}
      >
        <X className="w-4 h-4" aria-hidden />
        got it
      </button>
    </div>,
    document.body,
  );
}
