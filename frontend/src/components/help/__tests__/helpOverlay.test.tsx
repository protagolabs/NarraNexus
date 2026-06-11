/**
 * @file_name: helpOverlay.test.tsx
 * @date: 2026-06-11
 * @description: Tests for the multi-page help overlay — anchor
 * measurement/skip logic, rail layout non-overlap, page switching,
 * open/close behaviors, keyboard shortcut, wobble determinism, and
 * per-page manifest density.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { HelpOverlay } from '../HelpOverlay';
import { measureAnnotations, layoutAnnotations } from '../measure';
import type { MeasuredAnnotation } from '../measure';
import { HelpButton } from '../HelpButton';
import { CHAT_VIEW_PAGES } from '../helpContent';
import type { HelpAnnotation, HelpPage } from '../helpContent';
import { wobblyArrow, wobblyEllipse, wobblyLine } from '../wobble';

const NOTE_A: HelpAnnotation = {
  helpId: 'test.alpha',
  note: 'Alpha note',
  rail: 'left',
  priority: 1,
};
const NOTE_B: HelpAnnotation = {
  helpId: 'test.beta',
  note: 'Beta note',
  rail: 'left',
  priority: 2,
};

const PAGE_ONE: HelpPage = { id: 'one', label: 'Page One', annotations: [NOTE_A] };
const PAGE_TWO: HelpPage = { id: 'two', label: 'Page Two', annotations: [NOTE_B] };

function plantAnchor(helpId: string, rect: Partial<DOMRect>) {
  const el = document.createElement('button');
  el.setAttribute('data-help-id', helpId);
  el.getBoundingClientRect = () =>
    ({
      x: 10, y: 10, width: 40, height: 20,
      top: 10, left: 10, right: 50, bottom: 30,
      toJSON: () => ({}),
      ...rect,
    }) as DOMRect;
  document.body.appendChild(el);
  return el;
}

afterEach(() => {
  cleanup();
  document.querySelectorAll('[data-help-id^="test."]').forEach((el) => el.remove());
});

describe('measureAnnotations', () => {
  it('skips missing anchors and orders by priority', () => {
    plantAnchor('test.alpha', {});
    expect(measureAnnotations([NOTE_B, NOTE_A]).map((m) => m.helpId)).toEqual(['test.alpha']);
  });

  it('skips zero-size anchors (hidden controls)', () => {
    plantAnchor('test.alpha', { width: 0, height: 0 });
    expect(measureAnnotations([NOTE_A])).toEqual([]);
  });
});

describe('layoutAnnotations — rail stacking never overlaps', () => {
  it('stacks same-rail notes with vertical separation', () => {
    // Two anchors at the SAME y — naive placement would collide.
    const measured: MeasuredAnnotation[] = [
      { ...NOTE_A, rect: { x: 10, y: 100, width: 40, height: 20 } },
      { ...NOTE_B, rect: { x: 10, y: 102, width: 40, height: 20 } },
    ];
    const placed = layoutAnnotations(measured, 1280, 800);
    expect(placed).toHaveLength(2);
    const [a, b] = [...placed].sort((p, q) => p.noteY - q.noteY);
    expect(b.noteY).toBeGreaterThan(a.noteY + 20);
    // Same rail → same x column.
    expect(a.noteX).toBe(b.noteX);
  });
});

describe('HelpOverlay — pages', () => {
  it('renders first page notes, switches pages via tabs', () => {
    plantAnchor('test.alpha', {});
    plantAnchor('test.beta', { x: 200, y: 200, left: 200, top: 200, right: 240, bottom: 220 });
    render(<HelpOverlay open pages={[PAGE_ONE, PAGE_TWO]} onClose={vi.fn()} />);

    expect(screen.getByText('Alpha note')).toBeInTheDocument();
    expect(screen.queryByText('Beta note')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Page Two' }));
    expect(screen.getByText('Beta note')).toBeInTheDocument();
    expect(screen.queryByText('Alpha note')).not.toBeInTheDocument();
  });

  it('shows the fallback when a page has no visible anchors', () => {
    render(<HelpOverlay open pages={[PAGE_ONE]} onClose={vi.fn()} />);
    expect(screen.getByText(/Nothing to explain/i)).toBeInTheDocument();
  });

  it('closes via Escape, backdrop click and the centered got-it', () => {
    const onClose = vi.fn();
    render(<HelpOverlay open pages={[PAGE_ONE]} onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(screen.getByRole('dialog'));
    fireEvent.click(screen.getByRole('button', { name: 'Close guide' }));
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('renders nothing when closed', () => {
    render(<HelpOverlay open={false} pages={[PAGE_ONE]} onClose={vi.fn()} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('HelpButton', () => {
  it('opens on click and on the ? shortcut; ignores ? while typing', () => {
    render(
      <>
        <input aria-label="field" />
        <HelpButton pages={[PAGE_ONE]} />
      </>,
    );
    fireEvent.click(screen.getByLabelText('Explain this page'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: '?' });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });

    const input = screen.getByLabelText('field');
    input.focus();
    fireEvent.keyDown(input, { key: '?' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('wobble paths', () => {
  it('are deterministic and valid', () => {
    expect(wobblyArrow({ x: 0, y: 0 }, { x: 100, y: 50 })).toBe(
      wobblyArrow({ x: 0, y: 0 }, { x: 100, y: 50 }),
    );
    expect(wobblyEllipse(50, 50, 30, 15)).toBe(wobblyEllipse(50, 50, 30, 15));
    const d = wobblyLine({ x: 0, y: 0 }, { x: 10, y: 0 });
    expect(d.startsWith('M ')).toBe(true);
  });
});

describe('chat view pages manifest', () => {
  it('has exactly the three Owner-specified pages', () => {
    expect(CHAT_VIEW_PAGES.map((p) => p.label)).toEqual([
      'Agent Setup',
      'Interacting',
      'Teams & Bundles',
    ]);
  });

  it('respects ≤8 density per page with unique non-empty notes', () => {
    for (const page of CHAT_VIEW_PAGES) {
      expect(page.annotations.length).toBeLessThanOrEqual(8);
      const ids = page.annotations.map((a) => a.helpId);
      expect(new Set(ids).size).toBe(ids.length);
      for (const a of page.annotations) {
        expect(a.note.trim().length).toBeGreaterThan(0);
      }
    }
  });
});
