/**
 * @file_name: BookmarkStrip.tsx
 * @date: 2026-06-10
 * @description: Right-edge ~36px vertical bookmark strip.
 *
 * Two fixed BIG bookmarks (activity, agent) each with an icon and
 * 90°-rotated mono-11px uppercase label.  Small bookmarks appear below
 * their big one, derived from bookmarkStore via visibleSubBookmarks().
 *
 * Key-prefix routing:
 *   job:* / inbox  → activity big bookmark
 *   profile:*      → agent big bookmark
 *
 * Status visuals:
 *   running   → Loader2 spin spinner
 *   attention → carbon pulse  (var(--color-carbon) + animate-pulse)
 *   info      → static yellow dot (var(--color-yellow-500))
 *
 * Big bookmark aggregate:
 *   any 'attention' sub-bookmark under it → carbon pulse
 *   else any 'info' → static yellow dot
 *   Activity big bookmark shows numeric badge = failedJobs + inboxUnread when > 0
 *
 * Peek animation: sub-bookmark elements are keyed by bookmark key, so a
 * newly appearing key mounts a fresh node and its one-shot CSS mount
 * animation (var(--motion-medium) var(--ease-paper)) plays exactly once.
 */

import { Activity, User, Loader2 } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  useBookmarkStore,
  visibleSubBookmarks,
  type SubBookmark,
  type VisibleSubBookmarkEntry,
} from '@/stores/bookmarkStore';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type BookmarkTab = 'activity' | 'agent';

export interface BookmarkOpenTarget {
  tab: BookmarkTab;
  key?: string;
}

interface BookmarkStripProps {
  agentId: string;
  activeTab: BookmarkTab | null;
  onOpen: (target: BookmarkOpenTarget) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** True if entry is an overflow sentinel, not a real SubBookmark. */
function isOverflow(entry: VisibleSubBookmarkEntry): entry is { overflow: number } {
  return 'overflow' in entry;
}

/** Determine the aggregate status for a big bookmark given its sub-bookmarks.
 *  Returns 'attention' if any sub has attention, 'info' if any has info,
 *  'none' if only running/empty. */
function aggregateStatus(subs: SubBookmark[]): 'attention' | 'info' | 'none' {
  if (subs.some((s) => s.status === 'attention')) return 'attention';
  if (subs.some((s) => s.status === 'info')) return 'info';
  return 'none';
}

/** Route a sub-bookmark key to the big bookmark tab it belongs to. */
function keyToTab(key: string): BookmarkTab {
  if (key.startsWith('profile:')) return 'agent';
  return 'activity'; // job:* and inbox
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Status indicator rendered inside a sub-bookmark button. */
function SubStatusIndicator({ status }: { status: SubBookmark['status'] }) {
  if (status === 'running') {
    return (
      <Loader2
        className="w-2.5 h-2.5 animate-spin shrink-0"
        style={{ color: 'var(--text-secondary)' }}
        aria-hidden
      />
    );
  }
  if (status === 'attention') {
    return (
      <span
        className="w-2 h-2 rounded-full shrink-0 animate-pulse"
        style={{ background: 'var(--color-carbon)' }}
        aria-hidden
      />
    );
  }
  // info
  return (
    <span
      className="w-2 h-2 rounded-full shrink-0"
      style={{ background: 'var(--color-yellow-500)' }}
      aria-hidden
    />
  );
}

interface SmallBookmarkProps {
  entry: VisibleSubBookmarkEntry;
  tab: BookmarkTab;
  onOpen: (target: BookmarkOpenTarget) => void;
}

function SmallBookmark({ entry, tab, onOpen }: SmallBookmarkProps) {
  if (isOverflow(entry)) {
    return (
      <div
        className={cn(
          'flex items-center justify-center h-7 w-full',
          'text-[9px] font-[family-name:var(--font-mono)] tracking-[0.1em]',
          'animate-bookmark-peek',
        )}
        style={{ color: 'var(--text-tertiary)' }}
      >
        +{entry.overflow}
      </div>
    );
  }

  const sub = entry as SubBookmark;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={sub.label}
            className={cn(
              'flex items-center justify-center gap-1 h-7 w-full px-1',
              'rounded-sm transition-colors duration-100 cursor-pointer',
              'hover:bg-[var(--nm-paper-warm)]',
              'animate-bookmark-peek',
            )}
            onClick={() => onOpen({ tab, key: sub.key })}
          >
            <SubStatusIndicator status={sub.status} />
          </button>
        </TooltipTrigger>
        <TooltipContent side="left">{sub.label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

interface BigBookmarkProps {
  id: BookmarkTab;
  icon: typeof Activity;
  label: string;
  active: boolean;
  aggregated: 'attention' | 'info' | 'none';
  badge?: number;
  onOpen: (target: BookmarkOpenTarget) => void;
  expanded: boolean;
}

function BigBookmark({
  id,
  icon: Icon,
  label,
  active,
  aggregated,
  badge,
  onOpen,
  expanded,
}: BigBookmarkProps) {
  const showBadge = badge !== undefined && badge > 0;

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label}
            aria-expanded={expanded}
            data-help-id={`bookmarks.${id}`}
            className={cn(
              'relative flex flex-col items-center justify-center gap-1 w-full py-3',
              'cursor-pointer transition-colors duration-150 rounded-sm',
              'hover:bg-[var(--nm-paper-warm)]',
              active && 'bg-[var(--nm-paper-warm)]',
            )}
            onClick={() => onOpen({ tab: id })}
          >
            {/* Icon */}
            <div className="relative shrink-0">
              <Icon
                className={cn(
                  'w-4 h-4',
                  aggregated === 'attention' && 'animate-pulse',
                )}
                style={{
                  color:
                    aggregated === 'attention'
                      ? 'var(--color-carbon)'
                      : aggregated === 'info'
                        ? 'var(--color-yellow-500)'
                        : 'var(--text-secondary)',
                }}
                aria-hidden
              />
              {/* Numeric badge on icon */}
              {showBadge && (
                <span
                  aria-live="polite"
                  className={cn(
                    'absolute -top-1.5 -right-1.5',
                    'flex items-center justify-center',
                    'min-w-[14px] h-[14px] px-0.5',
                    'text-[8px] font-bold tabular-nums leading-none',
                    'rounded-full',
                  )}
                  style={{
                    background: 'var(--color-carbon)',
                    color: 'var(--nm-paper)',
                  }}
                >
                  {badge! > 99 ? '99+' : badge}
                </span>
              )}
            </div>

            {/* 90°-rotated label — same font language as ctx-tabs */}
            <span
              className={cn(
                'text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em]',
                'leading-none select-none whitespace-nowrap',
                '[writing-mode:vertical-rl] [text-orientation:mixed]',
                'rotate-180',
              )}
              style={{
                color:
                  aggregated === 'attention'
                    ? 'var(--color-carbon)'
                    : aggregated === 'info'
                      ? 'var(--color-yellow-500)'
                      : active
                        ? 'var(--text-primary)'
                        : 'var(--text-tertiary)',
              }}
            >
              {label}
            </span>

            {/* Info-only dot (no pulse) — shown when no badge and info status */}
            {aggregated === 'info' && !showBadge && (
              <span
                className="absolute top-2 right-2 w-1.5 h-1.5 rounded-full"
                style={{ background: 'var(--color-yellow-500)' }}
                aria-hidden
              />
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="left">{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function BookmarkStrip({ agentId, activeTab, onOpen }: BookmarkStripProps) {
  const agentState = useBookmarkStore((s) => s.agents[agentId]);


  if (!agentState) {
    // No state yet — render the strip with the two big bookmarks only.
    return (
      <div
        className="flex flex-col w-9 shrink-0 select-none"
        style={{ borderLeft: '1px solid var(--nm-hairline)' }}
      >
        <BigBookmark
          id="activity"
          icon={Activity}
          label="ACTIVITY"
          active={activeTab === 'activity'}
          aggregated="none"
          badge={0}
          onOpen={onOpen}
          expanded={activeTab === 'activity'}
        />
        <BigBookmark
          id="agent"
          icon={User}
          label="AGENT"
          active={activeTab === 'agent'}
          aggregated="none"
          onOpen={onOpen}
          expanded={activeTab === 'agent'}
        />
      </div>
    );
  }

  const { subBookmarks, badges, highlights } = agentState;

  // Split sub-bookmarks by big-bookmark routing
  const activitySubs = subBookmarks.filter((s) => keyToTab(s.key) === 'activity');
  const agentSubs = subBookmarks.filter((s) => keyToTab(s.key) === 'agent');

  const activityVisible = visibleSubBookmarks(
    { subBookmarks: activitySubs, highlights, badges },
    3,
  );
  const agentVisible = visibleSubBookmarks(
    { subBookmarks: agentSubs, highlights, badges },
    3,
  );

  const activityAgg = aggregateStatus(activitySubs);
  const agentAgg = aggregateStatus(agentSubs);

  const totalBadge = badges.failedJobs + badges.inboxUnread;

  return (
    <div
      className="flex flex-col w-9 shrink-0 select-none overflow-hidden"
      style={{ borderLeft: '1px solid var(--nm-hairline)' }}
    >
      {/* Activity big bookmark */}
      <BigBookmark
        id="activity"
        icon={Activity}
        label="ACTIVITY"
        active={activeTab === 'activity'}
        aggregated={activityAgg}
        badge={totalBadge > 0 ? totalBadge : undefined}
        onOpen={onOpen}
        expanded={activeTab === 'activity'}
      />

      {/* Activity sub-bookmarks */}
      {activityVisible.map((entry, idx) => {
        const key = isOverflow(entry) ? `__overflow_activity_${idx}` : entry.key;
        return (
          <SmallBookmark
            key={key}
            entry={entry}
            tab="activity"
            onOpen={onOpen}
          />
        );
      })}

      {/* Separator hairline between big bookmarks */}
      <div
        className="mx-1.5 my-1"
        style={{ height: '1px', background: 'var(--nm-hairline)' }}
      />

      {/* Agent big bookmark */}
      <BigBookmark
        id="agent"
        icon={User}
        label="AGENT"
        active={activeTab === 'agent'}
        aggregated={agentAgg}
        onOpen={onOpen}
        expanded={activeTab === 'agent'}
      />

      {/* Agent sub-bookmarks */}
      {agentVisible.map((entry, idx) => {
        const key = isOverflow(entry) ? `__overflow_agent_${idx}` : entry.key;
        return (
          <SmallBookmark
            key={key}
            entry={entry}
            tab="agent"
            onOpen={onOpen}
          />
        );
      })}
    </div>
  );
}
