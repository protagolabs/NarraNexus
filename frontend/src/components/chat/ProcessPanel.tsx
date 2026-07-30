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
 * Visual language (2026-07-30, after owner feedback that the first cut
 * was too flat): a real terminal frame — chrome header with a live
 * status dot, elapsed timer and op counter; per-species row glyphs
 * (`∴` thinking / `$` tool / `↳` output) with distinct ink levels and a
 * silicon-highlighted tool name; a blinking block cursor on the last
 * line. Everything uses theme tokens so both light and dark hold up.
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
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import type { TurnEvent } from '@/types';
import { cn } from '@/lib/utils';

export interface ProcessPanelProps {
  events: TurnEvent[];
}

/** Max panel height as a viewport fraction — any taller pushes the composer out of view. */
const MAX_HEIGHT_CLASS = 'max-h-[40vh]';

/** Within how many pixels of the bottom still counts as "following".
 *  One wheel notch is ~100px, so 24 separates "the user scrolled up"
 *  from browser scroll rounding error. */
const FOLLOW_THRESHOLD_PX = 24;

/** "mcp__chat_module__get_chat_history" → "get_chat_history" — same
 *  friendly-name rule TurnTimeline uses; the namespace is debug detail. */
function friendlyToolName(toolName: string): string {
  const parts = toolName.split('__');
  return parts[parts.length - 1] || toolName;
}

/** Ticks once a second from mount; the panel mounts when the turn
 *  starts, so this reads as the turn's elapsed time. */
function useElapsedSeconds(): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, []);
  return elapsed;
}

function formatElapsed(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

export const ProcessPanel = memo(function ProcessPanel({ events }: ProcessPanelProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const elapsed = useElapsedSeconds();

  const process = useMemo(
    () => events.filter(
      (e) => e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output',
    ),
    [events],
  );

  const toolCount = useMemo(
    () => process.filter((e) => e.type === 'tool_call').length,
    [process],
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

  const planDone = plan ? plan.steps.filter((s) => s.status === 'completed').length : 0;

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
      className="mb-2 overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--nm-paper)] shadow-sm"
      style={{ fontFamily: 'var(--font-mono)' }}
    >
      {/* Terminal chrome: live dot + title on the left, elapsed timer and
          op counter on the right. The dot pulses while mounted — mounted
          IS running, so no extra state is needed. */}
      <div
        className="flex items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--nm-paper-warm)] px-3 py-1.5"
      >
        <span className="relative flex h-2 w-2 shrink-0">
          <span
            className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
            style={{ background: 'var(--color-success)' }}
          />
          <span
            className="relative inline-flex h-2 w-2 rounded-full"
            style={{ background: 'var(--color-success)' }}
          />
        </span>
        <span
          className="text-[10px] uppercase tracking-[0.18em]"
          style={{ color: 'var(--nm-ink70)' }}
        >
          {t('chat.process.title', 'agent · process')}
        </span>
        <span className="ml-auto flex items-center gap-3 text-[10px] tabular-nums" style={{ color: 'var(--nm-ink50)' }}>
          <span>{toolCount} {t('chat.process.ops', 'ops')}</span>
          <span>{formatElapsed(elapsed)}</span>
        </span>
      </div>

      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          followRef.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_THRESHOLD_PX;
        }}
        className={cn(MAX_HEIGHT_CLASS, 'overflow-y-auto px-3 py-2 text-xs leading-relaxed')}
      >
        {process.map((event) => {
          if (event.type === 'thinking') {
            return (
              <div key={event.id} className="flex gap-2 py-0.5">
                <span aria-hidden="true" className="shrink-0 select-none" style={{ color: 'var(--nm-ink30)' }}>
                  ∴
                </span>
                <span className="whitespace-pre-wrap italic" style={{ color: 'var(--nm-ink50)' }}>
                  {event.content}
                </span>
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
                className="flex items-center gap-2 rounded px-1 py-0.5 -mx-1 hover:bg-[var(--nm-paper-warm)]"
              >
                {event.pending ? (
                  <Loader2
                    className="h-3 w-3 shrink-0 animate-spin"
                    style={{ color: 'var(--color-warning)' }}
                  />
                ) : (
                  <span
                    aria-hidden="true"
                    className="shrink-0 select-none font-semibold"
                    style={{ color: 'var(--color-success)' }}
                  >
                    $
                  </span>
                )}
                <span className="shrink-0 font-semibold" style={{ color: 'var(--color-silicon)' }}>
                  {friendlyToolName(event.tool_name)}
                </span>
                <span className="truncate" style={{ color: 'var(--nm-ink50)' }}>
                  {event.pending ? '…' : String(firstArg ?? '')}
                </span>
              </div>
            );
          }
          return (
            <div key={event.id} className="flex gap-2 py-0.5 pl-5">
              <span aria-hidden="true" className="shrink-0 select-none" style={{ color: 'var(--nm-ink30)' }}>
                ↳
              </span>
              <span className="truncate" style={{ color: 'var(--nm-ink50)' }}>
                {event.output}
              </span>
            </div>
          );
        })}
        {/* Live cursor — the terminal's "still running" heartbeat. */}
        <div aria-hidden="true" className="flex gap-2 py-0.5">
          <span className="select-none" style={{ color: 'var(--color-silicon)' }}>❯</span>
          <span
            className="inline-block w-2 animate-pulse select-none"
            style={{ color: 'var(--nm-ink70)' }}
          >
            ▌
          </span>
        </div>
      </div>

      {plan && (
        <div
          data-testid="process-plan"
          className="border-t border-[var(--border-subtle)] bg-[var(--nm-paper-warm)] px-3 py-2 text-xs"
        >
          <div className="mb-1.5 flex items-center gap-2">
            <span
              className="text-[10px] uppercase tracking-[0.18em]"
              style={{ color: 'var(--nm-ink50)' }}
            >
              {t('chat.process.plan', 'Plan')}
            </span>
            <span className="text-[10px] tabular-nums" style={{ color: 'var(--nm-ink50)' }}>
              {planDone}/{plan.steps.length}
            </span>
            {/* Mini progress bar: width follows completed/total. */}
            <span
              className="ml-1 h-1 flex-1 overflow-hidden rounded-full"
              style={{ background: 'var(--nm-ink30)', maxWidth: 96 }}
            >
              <span
                className="block h-full rounded-full transition-[width] duration-500"
                style={{
                  width: `${plan.steps.length ? Math.round((planDone / plan.steps.length) * 100) : 0}%`,
                  background: 'var(--color-success)',
                }}
              />
            </span>
          </div>
          <div className="space-y-0.5">
            {plan.steps.map((s, i) => {
              const active = s.status === 'in_progress';
              const done = s.status === 'completed';
              return (
                <div
                  key={`${i}-${s.step}`}
                  className="flex items-center gap-2"
                  style={{
                    color: active
                      ? 'var(--color-silicon)'
                      : done
                        ? 'var(--nm-ink50)'
                        : 'var(--nm-ink70)',
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  <span aria-hidden="true" className="shrink-0 select-none">
                    {done ? '✓' : active ? '▶' : '○'}
                  </span>
                  <span className={cn(done && 'line-through')}>{s.step}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
});
