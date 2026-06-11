/**
 * @file_name: HelpButton.tsx
 * @author:
 * @date: 2026-06-11
 * @description: The bottom-left "?" entry point for the help overlay.
 * Fixed-position circular button + the `?` keyboard shortcut (ignored
 * while typing in inputs). Owns the overlay open state.
 */

import { useEffect, useState } from 'react';
import { HelpCircle } from 'lucide-react';
import { HelpOverlay } from './HelpOverlay';
import type { HelpPage } from './helpContent';

interface HelpButtonProps {
  pages: HelpPage[];
}

function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return (
    t.tagName === 'INPUT' ||
    t.tagName === 'TEXTAREA' ||
    t.isContentEditable
  );
}

export function HelpButton({ pages }: HelpButtonProps) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '?' && !isTypingTarget(e.target)) {
        setOpen((v) => !v);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  return (
    <>
      <button
        type="button"
        aria-label="Explain this page"
        title="Explain this page (?)"
        onClick={() => setOpen((v) => !v)}
        className="fixed bottom-4 left-4 z-[150] flex items-center justify-center w-8 h-8 rounded-full cursor-pointer transition-all duration-150 hover:scale-110"
        style={{
          background: 'var(--nm-card)',
          border: '1px solid var(--nm-hairline)',
          boxShadow: 'var(--nm-elev-1)',
          color: 'var(--nm-ink50)',
        }}
      >
        <HelpCircle className="w-4 h-4" aria-hidden />
      </button>
      <HelpOverlay open={open} pages={pages} onClose={() => setOpen(false)} />
    </>
  );
}
