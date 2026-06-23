/**
 * @file_name: BookmarkStrip.tsx
 * @author:
 * @date: 2026-06-11
 * @description: Right-edge vertical bookmark strip — atomic-tab IA.
 *
 * Owner-decided structure: the smallest unit is an atomic tab (one tab =
 * one panel; centered icon + small caption so labels are readable without
 * tooltips). Categories from STRIP_CATEGORIES group the tabs; the groups
 * are divided by a hairline only (the mono text headers were dropped for a
 * cleaner, icon-only strip). Hover/active highlight in carbon (orange);
 * resting icons stay neutral so species color reads as accent, not noise.
 * Live signals from bookmarkStore render as a status overlay on the owning
 * tab: spinner (running), carbon pulse + count (attention), yellow dot (info).
 *
 * 64px wide; scrolls vertically if the window is short.
 */

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Loader2 } from 'lucide-react';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import {
  STRIP_CATEGORIES,
  deriveTabStatus,
  type AtomicTabId,
  type AtomicTabDef,
  type TabStatus,
} from './tabs';
import { cn } from '@/lib/utils';

export interface BookmarkStripProps {
  agentId: string;
  activeTab: AtomicTabId | null;
  onOpen: (tab: AtomicTabId) => void;
}

export function BookmarkStrip({ agentId, activeTab, onOpen }: BookmarkStripProps) {
  const agentState = useBookmarkStore((s) => s.agents[agentId]);

  return (
    <div
      className="flex flex-col w-16 shrink-0 select-none overflow-y-auto overflow-x-hidden py-1"
      style={{ borderLeft: '1px solid var(--nm-hairline)', scrollbarWidth: 'none' }}
      data-help-id="bookmarks.strip"
    >
      {STRIP_CATEGORIES.map((category, ci) => (
        <div key={category.label} className="flex flex-col items-stretch">
          {/* Every group (after the first) is separated by a hairline. Brand-
              spine groups (Narra/Nexus) additionally show a colored title. */}
          {ci > 0 && (
            <div className="mx-2 my-1.5 border-t border-[var(--nm-hairline)]" aria-hidden />
          )}
          {category.title && (
            <div className="px-1 pt-1.5 pb-1 text-center" aria-hidden>
              <span
                className="block text-[8px] font-medium font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] leading-none"
                style={{
                  color:
                    category.accent === 'silicon'
                      ? 'var(--color-silicon)'
                      : 'var(--color-carbon)',
                }}
              >
                {category.title}
              </span>
            </div>
          )}

          {category.tabs.map((tab) => (
            <AtomicTab
              key={tab.id}
              tab={tab}
              active={activeTab === tab.id}
              status={deriveTabStatus(agentState, tab.id)}
              onOpen={onOpen}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AtomicTab
// ---------------------------------------------------------------------------

interface AtomicTabProps {
  tab: AtomicTabDef;
  active: boolean;
  status: TabStatus;
  onOpen: (tab: AtomicTabId) => void;
}

function AtomicTab({ tab, active, status, onOpen }: AtomicTabProps) {
  const Icon = tab.icon;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={tab.label}
            aria-expanded={active}
            data-help-id={`bookmarks.${tab.id}`}
            onClick={() => onOpen(tab.id)}
            className={cn(
              'group relative flex flex-col items-center justify-center gap-0.5 h-11 w-full shrink-0',
              'cursor-pointer transition-colors duration-150',
              // No background highlight — only the icon + caption (and the
              // active edge rule) light up carbon.
            )}
            style={
              // Bookmark tongue: the active tab shows a 2px carbon rule on the
              // outer edge — the bit of the bookmark you can see from the
              // page edge.
              active ? { boxShadow: 'inset -2px 0 0 var(--color-carbon)' } : undefined
            }
          >
            <Icon
              className={cn(
                'w-4 h-4 transition-colors',
                status.status === 'attention'
                  ? 'text-[var(--color-carbon)] animate-pulse'
                  : active
                    ? 'text-[var(--color-carbon)]'
                    : 'text-[var(--text-tertiary)] group-hover:text-[var(--color-carbon)]',
              )}
              aria-hidden
            />

            {/* Status overlay — top-right corner of the tab */}
            {status.status === 'running' && (
              <Loader2
                className="absolute top-1 right-2 w-2.5 h-2.5 animate-spin"
                style={{ color: 'var(--color-yellow-500)' }}
                aria-hidden
              />
            )}
            {status.status === 'attention' && status.badge !== undefined && status.badge > 0 && (
              <span
                aria-live="polite"
                className="absolute top-0.5 right-1.5 flex items-center justify-center min-w-[13px] h-[13px] px-0.5 rounded-full text-[8px] font-bold tabular-nums leading-none"
                style={{ background: 'var(--color-carbon)', color: 'var(--color-gray-50)' }}
              >
                {status.badge > 99 ? '99+' : status.badge}
              </span>
            )}
            {status.status === 'info' && (
              <span
                className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full allow-circle"
                style={{ background: 'var(--color-yellow-500)' }}
                aria-hidden
              />
            )}

            {/* Caption — readable without a tooltip */}
            <span
              className={cn(
                'block max-w-full px-0.5 truncate text-center text-[8px] font-[family-name:var(--font-mono)] uppercase tracking-[0.06em] leading-none transition-colors',
                active
                  ? 'text-[var(--color-carbon)]'
                  : 'text-[var(--text-tertiary)] group-hover:text-[var(--color-carbon)]',
              )}
            >
              {tab.stripLabel ?? tab.label}
            </span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="left">{tab.label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
