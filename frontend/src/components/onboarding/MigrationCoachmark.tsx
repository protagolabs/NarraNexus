/**
 * @file_name: MigrationCoachmark.tsx
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: A coach-mark bubble that points at the sidebar "+" (Create menu),
 * shown once after the user dismisses the migration welcome modal via Later/X.
 *
 * Anchors to the `data-help-id="sidebar.create-agent"` element the same way the
 * HelpOverlay does (querySelector + getBoundingClientRect), and portals to body
 * so it is never clipped by the sidebar scroll container. Stays until the user
 * clicks it away ("挂到点掉"). Expanded-sidebar only for now — if the anchor is
 * absent (collapsed rail) it simply renders nothing.
 */

import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

const ANCHOR = '[data-help-id="sidebar.create-agent"]';

export function MigrationCoachmark({ onDismiss }: { onDismiss: () => void }) {
  const { t } = useTranslation();
  const [rect, setRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    const measure = () => {
      const el = document.querySelector(ANCHOR) as HTMLElement | null;
      const r = el?.getBoundingClientRect() ?? null;
      // Ignore a zero-size / offscreen anchor (collapsed sidebar, not mounted yet).
      setRect(r && r.width > 0 && r.height > 0 ? r : null);
    };
    measure();
    window.addEventListener('resize', measure);
    // The sidebar may mount/relayout after us; re-measure briefly.
    const id = window.setInterval(measure, 500);
    return () => {
      window.removeEventListener('resize', measure);
      window.clearInterval(id);
    };
  }, []);

  if (!rect) return null;

  return createPortal(
    <div
      style={{
        position: 'fixed',
        top: rect.top + rect.height / 2,
        left: rect.right + 10,
        transform: 'translateY(-50%)',
        zIndex: 60,
        maxWidth: 240,
      }}
      className={cn(
        'rounded-[var(--radius-sm)] border shadow-md',
        'border-[var(--nm-ink)] bg-[var(--nm-paper)] px-3 py-2',
      )}
    >
      {/* left-pointing arrow toward the "+" */}
      <span
        className="absolute h-2 w-2 rotate-45 border-b border-l bg-[var(--nm-paper)] border-[var(--nm-ink)]"
        style={{ left: -5, top: '50%', marginTop: -4 }}
      />
      <div className="text-xs text-[var(--nm-ink)]">
        {t('onboarding.migrationCoachmark.text')}
      </div>
      <button
        onClick={onDismiss}
        className="mt-1.5 text-[11px] font-medium text-[var(--nm-ink)] underline"
      >
        {t('onboarding.migrationCoachmark.gotIt')}
      </button>
    </div>,
    document.body,
  );
}
