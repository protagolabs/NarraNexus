/**
 * @file_name: BookmarkDrawer.tsx
 * @date: 2026-06-10
 * @description: Slide-over shell for bookmark panel content.
 *
 * Three renderings (Owner 2026-08-06):
 *   Mobile slide-over (pinned=false, inset=false): fixed overlay 440px,
 *     anchored `edgeReservePx` from the right edge.
 *     - transparent backdrop; click backdrop or Esc → onClose
 *     - role="dialog", NOT aria-modal — surrounding chrome stays operable
 *   Desktop transient (pinned=false, inset=true): IN-FLOW column of
 *     `insetWidth` (per-tab: artifacts wants ~50vw for readability, the
 *     rest 440px) — the chat shifts left instead of being covered. Still
 *     transient: backdrop click + Esc close it.
 *   Pinned (pinned=true): static column, laid out in the flex row by the
 *     parent. Owns its own frame + user-resizable persisted width; no
 *     backdrop, survives outside clicks.
 *
 * Header: mono uppercase title + Pin/PinOff toggle + X close.
 *
 * Toggling pin must not remount the panel (2026-07-30)
 * ----------------------------------------------------
 * React has no reparent primitive: moving a subtree to a different DOM parent
 * always unmounts and remounts it, discarding its state. So the ONLY way the
 * pin toggle can preserve what the user set up inside the panel (job filter,
 * view mode, expanded rows, scroll) is for the panel's DOM position to never
 * move at all. Two consequences, both load-bearing:
 *
 *   1. **No portal.** The slide-over is `position: fixed` (out of flow, so it
 *      consumes no layout space) rendered right where the pinned column would
 *      be. Wrapping in `createPortal` for one mode and not the other is itself
 *      a tree-shape change and remounts just the same — that was the first
 *      attempted fix, and the test suite caught it.
 *   2. **One element, stable slot.** Both modes are the SAME `<div>` with
 *      different classes/styles, in a fragment whose child positions don't
 *      shift. Don't "clean this up" into `if (pinned) return …` — two returns
 *      with different shapes remount the children.
 *
 * The no-portal choice does move the overlay into `<main>`'s stacking context
 * (`relative z-10`), so it no longer paints over the fixed sidebar (z-40).
 * They only overlap on mobile with the off-canvas nav open, where the nav
 * covering content is the expected behaviour anyway.
 *
 * The slide-in animation uses CSS via animate-slide-in-right (already
 * defined in the project's Tailwind/CSS config).  Pin state persistence
 * is the caller's responsibility (typically localStorage-backed in the
 * parent).
 */

import { type ReactNode, type Ref, useEffect, useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Pin, PinOff, HelpCircle, ChevronDown, Check } from 'lucide-react';
import { useDismissOnOutside } from '@/hooks';
import { cn } from '@/lib/utils';
import { STRIP_CATEGORIES, type AtomicTabId } from './tabs';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface BookmarkDrawerProps {
  open: boolean;
  pinned: boolean;
  onPinnedChange: (pinned: boolean) => void;
  onClose: () => void;
  title: string;
  /**
   * Width of the right edge the slide-over must NOT cover — the bookmark
   * strip plus the layout gutter (see MainLayout). Both the panel and the
   * click-capturing backdrop are inset by this much, which is what makes
   * "click another tab" switch panels in ONE click instead of requiring the
   * user to close this one first. Ignored in pinned mode. Default 0.
   */
  edgeReservePx?: number;
  /** Column width in px, pinned mode only. Ignored by the slide-over. */
  pinnedWidth?: number;
  /**
   * Desktop: the transient (unpinned) drawer renders as an IN-FLOW column —
   * content shifts left instead of being covered (a fixed overlay hides the
   * chat's own-message avatars). It keeps transient semantics: backdrop
   * click + Esc close it. Overlay mode remains for mobile (inset=false),
   * where there is no room to share.
   */
  inset?: boolean;
  /**
   * Column width for the transient inset mode — any CSS width (px number
   * or e.g. 'min(50vw, …)' for the artifacts panel, which wants half the
   * screen for readability). Pinned mode keeps using pinnedWidth (the
   * user-resizable, persisted one). Default 440.
   */
  insetWidth?: number | string;
  /**
   * One-sentence explainer for the open panel (what it's for + how to use
   * it), shown behind a ? icon beside the title. Empty string hides the
   * icon. Localized by the caller (tabDescKey).
   */
  description?: string;
  /**
   * Handle on the pinned column, so the parent's ResizableDivider can write
   * `width` straight to the DOM during a drag. Null in slide-over mode.
   */
  columnRef?: Ref<HTMLDivElement>;
  /**
   * The open tab + switcher callback. When provided, the header title
   * becomes a dropdown listing every panel (the tabs registry), so the
   * drawer can change its own content without a trip to the chat header —
   * a pinned drawer is an independent window and owns its own controls.
   */
  activeTab?: AtomicTabId | null;
  onSelectTab?: (id: AtomicTabId) => void;
  /** Optional banner rendered between the header and the panel content
   *  (e.g. the first-run coach mark). */
  banner?: ReactNode;
  children: ReactNode;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function BookmarkDrawer({
  open,
  pinned,
  onPinnedChange,
  onClose,
  title,
  edgeReservePx = 0,
  pinnedWidth = 400,
  inset = false,
  insetWidth = 440,
  description = '',
  columnRef,
  activeTab = null,
  onSelectTab,
  banner,
  children,
}: BookmarkDrawerProps) {
  // Keyboard Esc handler — only for slide-over mode (not pinned)
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !pinned) {
        onClose();
      }
    },
    [onClose, pinned],
  );

  useEffect(() => {
    if (open && !pinned) {
      document.addEventListener('keydown', handleKeyDown);
      return () => {
        document.removeEventListener('keydown', handleKeyDown);
      };
    }
  }, [open, pinned, handleKeyDown]);

  if (!open) return null;

  // Transient (unpinned) still closes on backdrop/Esc; but on desktop
  // (inset) it lays out IN FLOW like the pinned column, so it never covers
  // chat content. True fixed overlay remains mobile-only.
  const transient = !pinned;
  const overlay = transient && !inset;

  return (
    <>
      {/* Transparent backdrop — transient mode only. It captures outside
          clicks; the in-flow panel below sits above it (z-[201]) so its own
          clicks are never eaten. When pinned this renders as `false`, which
          is fine: the panel below keeps a stable slot in this fragment
          either way. */}
      {transient && (
        <div
          className="fixed inset-y-0 left-0 z-[200]"
          style={{ right: edgeReservePx }}
          data-drawer-backdrop=""
          onClick={onClose}
        />
      )}

      {/* The panel. ONE element for all modes — only its positioning changes
          (mode switches must never remount the children; see file header).
          Mobile slide-over is `position: fixed`; desktop transient + pinned
          are in-flow flex columns. NOT aria-modal in overlay mode: the
          surrounding chrome stays operable. */}
      <div
        ref={columnRef}
        role={transient ? 'dialog' : undefined}
        aria-label={transient ? title : undefined}
        className={cn(
          'flex flex-col overflow-hidden',
          overlay
            ? 'fixed inset-y-0 z-[200] animate-slide-in-right'
            : transient
              ? 'shrink-0 relative z-[201] animate-slide-in-right'
              : 'shrink-0 rounded-[var(--radius-md)]',
        )}
        style={
          overlay
            ? {
                right: edgeReservePx,
                width: `min(440px, 100vw - ${edgeReservePx}px)`,
                background: 'var(--nm-paper)',
                boxShadow: '-2px 0 var(--nm-elev-edge)',
              }
            : transient
              ? {
                  width: insetWidth,
                  background: 'var(--nm-paper)',
                  borderLeft: '1px solid var(--nm-hairline)',
                }
              : {
                  width: pinnedWidth,
                  background: 'var(--nm-paper)',
                  border: '1px solid var(--nm-hairline)',
                }
        }
        onClick={transient ? (e) => e.stopPropagation() : undefined}
      >
        <DrawerHeader
          title={title}
          description={description}
          pinned={pinned}
          onPinnedChange={onPinnedChange}
          onClose={onClose}
          activeTab={activeTab}
          onSelectTab={onSelectTab}
        />
        {banner}
        <div className="flex-1 min-h-0 overflow-y-auto">{children}</div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Header sub-component
// ---------------------------------------------------------------------------

interface DrawerHeaderProps {
  title: string;
  description?: string;
  pinned: boolean;
  onPinnedChange: (pinned: boolean) => void;
  onClose: () => void;
  activeTab?: AtomicTabId | null;
  onSelectTab?: (id: AtomicTabId) => void;
}

const TITLE_CLASS =
  'text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] leading-none truncate';

function DrawerHeader({
  title,
  description,
  pinned,
  onPinnedChange,
  onClose,
  activeTab,
  onSelectTab,
}: DrawerHeaderProps) {
  const { t } = useTranslation();
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const switcherRef = useDismissOnOutside<HTMLDivElement>(switcherOpen, () => setSwitcherOpen(false));
  return (
    <div
      className="flex items-center justify-between gap-2 px-4 py-3 shrink-0"
      style={{ borderBottom: '1px solid var(--nm-hairline)' }}
    >
      <div className="flex items-center gap-1.5 min-w-0">
        {onSelectTab ? (
          /* Title as a panel switcher: the drawer owns what it shows. */
          <div ref={switcherRef} className="relative min-w-0">
            <button
              type="button"
              onClick={() => setSwitcherOpen((v) => !v)}
              aria-expanded={switcherOpen}
              aria-label={t('bookmarks.drawer.switchPanel')}
              title={t('bookmarks.drawer.switchPanel')}
              className="flex items-center gap-1 min-w-0 rounded-[var(--radius-xs)] px-1 -mx-1 py-0.5 transition-colors hover:bg-[var(--nm-paper-warm)]"
            >
              <span className={TITLE_CLASS} style={{ color: 'var(--text-primary)' }}>
                {title}
              </span>
              <ChevronDown
                className={cn('w-3 h-3 shrink-0 text-[var(--nm-ink50)] transition-transform', switcherOpen && 'rotate-180')}
                aria-hidden
              />
            </button>
            {switcherOpen && (
              <div
                className="absolute left-0 top-full z-50 mt-1.5 w-52 max-h-[60vh] overflow-y-auto rounded-[var(--radius-md)] border py-1 shadow-lg"
                style={{ background: 'var(--nm-card)', borderColor: 'var(--nm-hairline)' }}
              >
                {STRIP_CATEGORIES.map((cat) => (
                  <div key={cat.label}>
                    <div className="px-3 pt-2 pb-1 text-[9px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] text-[var(--nm-ink30)]">
                      {t(cat.labelKey)}
                    </div>
                    {cat.tabs.map(({ id, labelKey, icon: Icon }) => {
                      const active = id === activeTab;
                      return (
                        <button
                          key={id}
                          type="button"
                          onClick={() => {
                            setSwitcherOpen(false);
                            if (!active) onSelectTab(id);
                          }}
                          className={cn(
                            'w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-left transition-colors',
                            'hover:bg-[var(--nm-paper-warm)]',
                            active ? 'text-[var(--nm-ink)] font-medium' : 'text-[var(--nm-ink70)]',
                          )}
                        >
                          <Icon className="w-3.5 h-3.5 shrink-0" aria-hidden />
                          <span className="flex-1 min-w-0 truncate">{t(labelKey)}</span>
                          {active && <Check className="w-3 h-3 shrink-0" aria-hidden />}
                        </button>
                      );
                    })}
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <span className={TITLE_CLASS} style={{ color: 'var(--text-primary)' }}>
            {title}
          </span>
        )}
        {/* ? explainer — one sentence on what this panel is for, for users
            who never open the docs. Hover/focus reveals a styled tooltip;
            localized upstream. */}
        {description && (
          <span className="group/help relative inline-flex shrink-0">
            <button
              type="button"
              aria-label={description}
              className="flex h-4 w-4 items-center justify-center rounded-full text-[var(--nm-ink30)] transition-colors hover:text-[var(--nm-ink70)] focus:outline-none focus-visible:text-[var(--nm-ink70)]"
            >
              <HelpCircle className="h-3.5 w-3.5" />
            </button>
            <span
              role="tooltip"
              className="pointer-events-none absolute left-0 top-full z-50 mt-1.5 w-max max-w-[280px] rounded-[var(--radius-sm)] px-2.5 py-2 text-[11px] leading-relaxed opacity-0 transition-opacity duration-100 group-hover/help:opacity-100 group-focus-within/help:opacity-100"
              style={{
                background: 'var(--nm-ink)',
                color: 'var(--nm-paper)',
              }}
            >
              {description}
            </span>
          </span>
        )}
      </div>

      <div className="flex items-center gap-1">
        {/* Pin / PinOff toggle */}
        {pinned ? (
          <button
            type="button"
            aria-label={t('bookmarks.drawer.unpin')}
            // The label was already written; it just wasn't
            // reachable by a mouse. Hovering explained nothing.
            title={t('bookmarks.drawer.unpin')}
            className={cn(
              'flex items-center justify-center w-6 h-6 rounded-[var(--radius-sm)]',
              'transition-colors duration-100 cursor-pointer',
              'hover:bg-[var(--nm-paper-warm)]',
            )}
            onClick={() => onPinnedChange(false)}
          >
            <PinOff
              className="w-3.5 h-3.5"
              style={{ color: 'var(--text-secondary)' }}
              aria-hidden
            />
          </button>
        ) : (
          <button
            type="button"
            aria-label={t('bookmarks.drawer.pin')}
            // The label was already written; it just wasn't
            // reachable by a mouse. Hovering explained nothing.
            title={t('bookmarks.drawer.pin')}
            className={cn(
              'flex items-center justify-center w-6 h-6 rounded-[var(--radius-sm)]',
              'transition-colors duration-100 cursor-pointer',
              'hover:bg-[var(--nm-paper-warm)]',
            )}
            onClick={() => onPinnedChange(true)}
          >
            <Pin
              className="w-3.5 h-3.5"
              style={{ color: 'var(--text-secondary)' }}
              aria-hidden
            />
          </button>
        )}

        {/* Close button */}
        <button
          type="button"
          aria-label={t('bookmarks.drawer.close')}
            // The label was already written; it just wasn't
            // reachable by a mouse. Hovering explained nothing.
            title={t('bookmarks.drawer.close')}
          className={cn(
            'flex items-center justify-center w-6 h-6 rounded-[var(--radius-sm)]',
            'transition-colors duration-100 cursor-pointer',
            'hover:bg-[var(--nm-paper-warm)]',
          )}
          onClick={onClose}
        >
          <X
            className="w-3.5 h-3.5"
            style={{ color: 'var(--text-secondary)' }}
            aria-hidden
          />
        </button>
      </div>
    </div>
  );
}
