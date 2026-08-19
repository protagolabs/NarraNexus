/**
 * @file_name: GuideAgentCoachmark.tsx
 * @author: Bin Liang
 * @date: 2026-08-19
 * @description: A one-shot coach-mark bubble for brand-new users, pointing at
 * the sidebar "+" (Create menu): their first agent (the onboarding guide) was
 * auto-created server-side, so this is the nudge that they can create more
 * themselves. Shown while lib/guideCoachmark reports 'pending' (set by the
 * login path when the backend says is_new_user); clicking it away writes
 * 'done' and it never returns.
 *
 * Anchoring/portal mechanics mirror MigrationCoachmark: measure the
 * `data-help-id="sidebar.create-agent"` element, portal to body so the
 * sidebar scroll container can't clip it, render nothing while the anchor is
 * absent (collapsed rail).
 */

import { useState, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import {
  dismissGuideCoachmark,
  isGuideCoachmarkPending,
} from '@/lib/guideCoachmark';

const ANCHOR = '[data-help-id="sidebar.create-agent"]';

export function GuideAgentCoachmark() {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(() => isGuideCoachmarkPending());
  const [rect, setRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    if (!visible) return;
    let intervalId = 0;
    let attempts = 0;
    const stop = () => {
      if (intervalId) { window.clearInterval(intervalId); intervalId = 0; }
    };
    const measure = () => {
      const el = document.querySelector(ANCHOR) as HTMLElement | null;
      const r = el?.getBoundingClientRect() ?? null;
      const next = r && r.width > 0 && r.height > 0 ? r : null;
      setRect((prev) => {
        if (prev && next && prev.top === next.top && prev.right === next.right) return prev;
        return next;
      });
      // The interval only wins the sidebar-mount race; stop once anchored, or
      // after ~10s if the sidebar stays collapsed.
      attempts += 1;
      if (next || attempts >= 20) stop();
    };
    measure();
    window.addEventListener('resize', measure);
    intervalId = window.setInterval(measure, 500);
    return () => {
      window.removeEventListener('resize', measure);
      stop();
    };
  }, [visible]);

  if (!visible || !rect) return null;

  const dismiss = () => {
    dismissGuideCoachmark();
    setVisible(false);
  };

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
        {t('onboarding.guideCoachmark.text')}
      </div>
      <button
        onClick={dismiss}
        className="mt-1.5 text-[11px] font-medium text-[var(--nm-ink)] underline"
      >
        {t('onboarding.guideCoachmark.gotIt')}
      </button>
    </div>,
    document.body,
  );
}
