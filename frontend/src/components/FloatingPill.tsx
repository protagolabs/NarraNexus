/**
 * @file_name: FloatingPill.tsx
 * @date: 2026-11-02
 * @description: The top-center floating notice pill shared by the app-wide update banners.
 *
 * One surface, one place: UpdateBanner (desktop updater) and WebUpdateBanner
 * (cloud redeploy) used to carry hand-copied class strings for the same pill.
 *
 * Stacking is by DOM order, not by a bespoke z value: the pill sits at the
 * shared `z-50` layer, so whatever renders later at that layer wins. App mounts
 * the banners after the amber top banners (the pill stays readable over them)
 * and before the routes (full-screen dialogs, also `z-50`, cover the pill —
 * the user must not hit Reload by accident while filling a dialog).
 *
 * The wrapper ignores pointer events and only the pill takes them, so clicks
 * beside the pill reach the workspace underneath.
 */

import type { ReactNode } from 'react';
import { X, type LucideIcon } from 'lucide-react';

interface FloatingPillProps {
  icon: LucideIcon;
  children: ReactNode;
  actionLabel: ReactNode;
  onAction: () => void;
  onDismiss: () => void;
  dismissLabel: string;
  dismissTitle?: string;
}

export function FloatingPill({
  icon: Icon,
  children,
  actionLabel,
  onAction,
  onDismiss,
  dismissLabel,
  dismissTitle,
}: FloatingPillProps) {
  return (
    <div
      className="fixed top-3 left-0 right-0 z-50 flex justify-center pointer-events-none font-[family-name:var(--font-sans)]"
      role="status"
      aria-live="polite"
    >
      <div className="pointer-events-auto flex items-center gap-3 px-4 py-2 rounded-full shadow-lg border border-[var(--nm-line)] bg-[var(--bg-elev)] text-[var(--nm-ink)] text-sm">
        <Icon className="w-4 h-4 text-[var(--accent-primary)]" aria-hidden="true" />
        <span>{children}</span>
        <button
          type="button"
          onClick={onAction}
          className="px-3 py-1 rounded-full text-xs font-medium bg-[var(--accent-primary)] text-white hover:opacity-90 transition"
        >
          {actionLabel}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          aria-label={dismissLabel}
          title={dismissTitle}
          className="p-1 rounded-full text-[var(--nm-ink50)] hover:text-[var(--nm-ink)] hover:bg-[var(--nm-line)]/40 transition"
        >
          <X className="w-3.5 h-3.5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
