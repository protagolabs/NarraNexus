/**
 * ProcessPanel — while the agent works, the process lives here; the
 * answer lives in the bubbles above.
 *
 * Why a separate panel: process and replies used to share one
 * chronological TurnTimeline, tiered only by solid-vs-dashed borders.
 * Nothing was missing, but the reader had to find the answer inside the
 * noise. Split out, the bubble carries only the answer and the process
 * scrolls here continuously — scannable like a terminal, not something
 * to read.
 *
 * The plan is pinned below the scroll area: it answers "where are we
 * now" and must not scroll away. Plans are full snapshots
 * (replace-on-write), so only the latest one renders.
 *
 * Mounted only while streaming; ChatPanel unmounts it when the turn
 * ends and the process folds back into each reply's bubble (see
 * lib/segmentTurn). So nothing is persisted here — this is a viewport,
 * not storage.
 */
import { memo, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import type { TurnEvent } from '@/types';
import { cn } from '@/lib/utils';

export interface ProcessPanelProps {
  events: TurnEvent[];
}

const PLAN_MARK: Record<string, string> = {
  completed: '✅',
  in_progress: '▶',
  pending: '○',
};

/** Max panel height as a viewport fraction — any taller pushes the composer out of view. */
const MAX_HEIGHT_CLASS = 'max-h-[40vh]';

/** Within how many pixels of the bottom still counts as "following".
 *  One wheel notch is ~100px, so 24 separates "the user scrolled up"
 *  from browser scroll rounding error. */
const FOLLOW_THRESHOLD_PX = 24;

export const ProcessPanel = memo(function ProcessPanel({ events }: ProcessPanelProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);

  const process = useMemo(
    () => events.filter(
      (e) => e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output',
    ),
    [events],
  );

  // Plans are full snapshots: each update replaces the previous one
  // wholesale, so take the last.
  const plan = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const e = events[i];
      if (e.type === 'plan') return e;
    }
    return undefined;
  }, [events]);

  // Auto-scroll to the bottom unless the user scrolled up — the same
  // bargain the message area makes: following is the default, but once
  // someone says "I want to look up there", never steal the viewport.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [process, plan]);

  if (process.length === 0 && !plan) return null;

  return (
    <div
      data-testid="process-panel"
      className="mb-2 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-elevated)]"
    >
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          followRef.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_THRESHOLD_PX;
        }}
        className={cn(
          MAX_HEIGHT_CLASS,
          'space-y-1 overflow-y-auto px-3 py-2 font-mono text-xs',
        )}
      >
        {process.map((event) => {
          if (event.type === 'thinking') {
            return (
              <div
                key={event.id}
                className="whitespace-pre-wrap text-[var(--text-tertiary)]"
              >
                · {event.content}
              </div>
            );
          }
          if (event.type === 'tool_call') {
            // Show the first argument value as a one-line summary; an
            // ellipsis until arguments arrive — the visible form of
            // pending: name decided, arguments still being written.
            const firstArg = Object.values(event.tool_input ?? {})[0];
            return (
              <div
                key={event.id}
                data-testid={`tool-row-${event.id}`}
                data-pending={event.pending ? 'true' : 'false'}
                className="flex items-center gap-2 text-[var(--text-secondary)]"
              >
                {event.pending ? (
                  <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
                ) : (
                  <span className="shrink-0">⚙</span>
                )}
                <span className="text-[var(--accent-primary)]">{event.tool_name}</span>
                <span className="truncate text-[var(--text-tertiary)]">
                  {event.pending ? '…' : String(firstArg ?? '')}
                </span>
              </div>
            );
          }
          return (
            <div key={event.id} className="truncate pl-4 text-[var(--text-tertiary)]">
              ↳ {event.output}
            </div>
          );
        })}
      </div>

      {plan && (
        <div
          data-testid="process-plan"
          className="space-y-0.5 border-t border-[var(--border-subtle)] px-3 py-2 text-xs"
        >
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">
            {t('chat.process.plan', 'Plan')}
          </div>
          {plan.steps.map((s, i) => (
            <div
              key={`${i}-${s.step}`}
              className={cn(
                'flex gap-2',
                s.status === 'in_progress' && 'text-[var(--accent-primary)]',
                s.status === 'completed' && 'text-[var(--text-tertiary)] line-through',
              )}
            >
              <span className="shrink-0">{PLAN_MARK[s.status] ?? '○'}</span>
              <span>{s.step}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
