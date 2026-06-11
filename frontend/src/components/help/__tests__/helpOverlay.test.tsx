/**
 * @file_name: helpOverlay.test.tsx
 * @date: 2026-06-11
 * @description: Tests for the hand-annotated help overlay — anchor
 * measurement/skip logic, open/close behaviors, keyboard shortcut, and
 * wobble path determinism.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { HelpOverlay } from '../HelpOverlay';
import { measureAnnotations } from '../measure';
import { HelpButton } from '../HelpButton';
import { CHAT_VIEW_ANNOTATIONS } from '../helpContent';
import type { HelpAnnotation } from '../helpContent';
import { wobblyArrow, wobblyEllipse, wobblyLine } from '../wobble';

const NOTE_A: HelpAnnotation = {
  helpId: 'test.alpha',
  note: 'Alpha note',
  side: 'right',
  priority: 1,
};
const NOTE_B: HelpAnnotation = {
  helpId: 'test.beta',
  note: 'Beta note',
  side: 'left',
  priority: 2,
};

function plantAnchor(helpId: string, rect: Partial<DOMRect>) {
  const el = document.createElement('button');
  el.setAttribute('data-help-id', helpId);
  el.getBoundingClientRect = () =>
    ({
      x: 10,
      y: 10,
      width: 40,
      height: 20,
      top: 10,
      left: 10,
      right: 50,
      bottom: 30,
      toJSON: () => ({}),
      ...rect,
    }) as DOMRect;
  document.body.appendChild(el);
  return el;
}

afterEach(() => {
  cleanup();
  document
    .querySelectorAll('[data-help-id^="test."]')
    .forEach((el) => el.remove());
});

describe('measureAnnotations', () => {
  it('skips annotations whose anchor is missing', () => {
    plantAnchor('test.alpha', {});
    const measured = measureAnnotations([NOTE_A, NOTE_B]);
    expect(measured.map((m) => m.helpId)).toEqual(['test.alpha']);
  });

  it('skips zero-size anchors (hidden controls)', () => {
    plantAnchor('test.alpha', { width: 0, height: 0 });
    expect(measureAnnotations([NOTE_A])).toEqual([]);
  });

  it('orders by priority', () => {
    plantAnchor('test.alpha', {});
    plantAnchor('test.beta', { x: 100, left: 100, right: 140 });
    const measured = measureAnnotations([NOTE_B, NOTE_A]);
    expect(measured.map((m) => m.helpId)).toEqual(['test.alpha', 'test.beta']);
  });
});

describe('HelpOverlay', () => {
  it('renders notes for present anchors and the fallback when none', () => {
    const onClose = vi.fn();
    render(<HelpOverlay open annotations={[NOTE_A]} onClose={onClose} />);
    expect(screen.getByText(/Nothing to explain/i)).toBeInTheDocument();
    cleanup();

    plantAnchor('test.alpha', {});
    render(<HelpOverlay open annotations={[NOTE_A]} onClose={onClose} />);
    expect(screen.getByText('Alpha note')).toBeInTheDocument();
  });

  it('closes on Escape and on backdrop click', () => {
    const onClose = vi.fn();
    render(<HelpOverlay open annotations={[]} onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('dialog'));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('renders nothing when closed', () => {
    render(<HelpOverlay open={false} annotations={[NOTE_A]} onClose={vi.fn()} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('HelpButton', () => {
  it('opens the overlay on click and on the ? shortcut', () => {
    render(<HelpButton annotations={[]} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Explain this page'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: '?' });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('ignores the ? shortcut while typing in an input', () => {
    render(
      <>
        <input aria-label="field" />
        <HelpButton annotations={[]} />
      </>,
    );
    const input = screen.getByLabelText('field');
    input.focus();
    fireEvent.keyDown(input, { key: '?' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('wobble paths', () => {
  it('are deterministic for identical inputs', () => {
    const a = wobblyArrow({ x: 0, y: 0 }, { x: 100, y: 50 });
    const b = wobblyArrow({ x: 0, y: 0 }, { x: 100, y: 50 });
    expect(a).toBe(b);
    expect(wobblyEllipse(50, 50, 30, 15)).toBe(wobblyEllipse(50, 50, 30, 15));
    expect(wobblyLine({ x: 0, y: 0 }, { x: 10, y: 0 })).toBe(
      wobblyLine({ x: 0, y: 0 }, { x: 10, y: 0 }),
    );
  });

  it('produce valid-looking SVG path data', () => {
    const d = wobblyArrow({ x: 5, y: 5 }, { x: 80, y: 40 });
    expect(d.startsWith('M ')).toBe(true);
    expect(d).toContain('Q ');
  });
});

describe('chat view manifest', () => {
  it('respects the ≤8 density discipline (spec §12.5)', () => {
    expect(CHAT_VIEW_ANNOTATIONS.length).toBeLessThanOrEqual(8);
  });

  it('has unique helpIds and non-empty notes', () => {
    const ids = CHAT_VIEW_ANNOTATIONS.map((a) => a.helpId);
    expect(new Set(ids).size).toBe(ids.length);
    for (const a of CHAT_VIEW_ANNOTATIONS) {
      expect(a.note.trim().length).toBeGreaterThan(0);
    }
  });
});
