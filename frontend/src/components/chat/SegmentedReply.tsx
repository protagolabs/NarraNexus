/**
 * SegmentedReply — render a turn's Segment[] as the times the agent
 * actually spoke.
 *
 * A turn may speak several times (m of its n tool calls are replies to
 * the user). The backend still stores one record per turn; this renders
 * that one record as m bubbles, each carrying the process that led to
 * it.
 *
 * One component serves both live and history, differing by one switch:
 *   - live (showProcess=false): the process is in the ProcessPanel
 *     above the composer — only answers render here, or the same
 *     process would paint twice;
 *   - settled (showProcess=true): the panel has unmounted and the
 *     process folds back onto each segment's own bubble.
 *
 * Since 2026-08-30 the settled case has two shapes: promoted — the process
 * renders inline, no drawer, because the narration is meant to be read — or
 * the original single collapsed "Reasoning & tools" affordance. A segment is
 * promoted only when the narration preference is on AND the segment actually
 * contains narration, so turns from drivers that emit none keep the compact
 * shape they always had. Same events either way.
 *
 * How segments are cut is lib/segmentTurn's job — this only draws.
 */
import { memo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronRight } from 'lucide-react';
import type { Segment } from '@/types';
import { Markdown } from '@/components/ui';
import { cn } from '@/lib/utils';
import { TurnTimeline } from './TurnTimeline';
import { useNarrationTier } from '@/hooks/useNarrationTier';

export interface SegmentedReplyProps {
  segments: Segment[];
  /** Whether the collapsed process details are available (true for history bubbles, false live). */
  showProcess?: boolean;
  /**
   * Start with every process region expanded. Used when the user already
   * clicked once to get here (the "View reasoning" fetch on a historical
   * bubble) — landing on another collapsed toggle would make it two
   * clicks to see anything. Regions can still be collapsed by hand.
   */
  defaultOpen?: boolean;
  /** Live: the last segment is still growing — give it a streaming cursor. */
  isStreaming?: boolean;
}

type FallbackKind = 'none' | 'no_reply' | 'after_error';

// Formerly TurnTimeline's ReplyBlock badge logic; it moved here with the
// answer tier (2026-07-30). Legacy 'helper_llm_fallback' is the
// pre-rename (2026-05-25) persisted value of helper_llm_no_reply —
// treated the same so old rows still show the recovery badge instead of
// nothing.
function fallbackKindFromReplyVia(via: string | undefined): FallbackKind {
  if (via === 'helper_llm_after_error') return 'after_error';
  if (via === 'helper_llm_no_reply' || via === 'helper_llm_fallback') return 'no_reply';
  return 'none';
}

export const SegmentedReply = memo(function SegmentedReply({
  segments,
  showProcess = false,
  defaultOpen = false,
  isStreaming = false,
}: SegmentedReplyProps) {
  const { t } = useTranslation();
  // Promote the process out of the drawer (2026-08-30) — but only for a turn
  // that actually HAS narration. A claude/codex-driver turn carries no
  // monologue at all (AgentThinking.monologue is empty for every
  // non-NexusPower driver), so promoting it would hand a whole class of
  // agents a different transcript shape in exchange for nothing, driven by a
  // setting whose name says "Progress narration". Those keep their drawer —
  // and inside it their reasoning opens with the drawer, so reading it still
  // costs the one click it always did.
  // Turning the preference off restores the drawer everywhere.
  const narrationEnabled = useNarrationTier();
  // Decided ONCE for the whole turn, not per segment: a NexusPower turn whose
  // first segment narrates and whose second is reasoning + tools only would
  // otherwise render half inline and half behind a drawer — two transcript
  // shapes stacked in one reply, which reads as a rendering bug.
  const promoted =
    narrationEnabled &&
    segments.some((s) => s.process.some((e) => e.type === 'thinking' && e.monologue));
  // Expansion state is keyed by segment index and lives here: the parent
  // re-renders on every streaming delta, so keeping it local is what
  // stops it from being reset.
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  return (
    <div className="space-y-3">
      {segments.map((segment, index) => {
        const isLast = index === segments.length - 1;
        const open = expanded[index] ?? defaultOpen;
        return (
          <div key={index} className="space-y-1">
            {showProcess && segment.process.length > 0 && (
              promoted ? (
                // Promoted layout: the process IS the transcript. The
                // narration sentences read at near-body weight, tool cards
                // sit inline, and each reasoning block collapses on its own
                // (TurnTimeline decides per block). No outer drawer — the
                // whole point is that the user reads the run without
                // clicking anything.
                <div data-testid={`segment-details-${index}`}>
                  <TurnTimeline events={segment.process} />
                </div>
              ) : (
                // Preference off: exactly the pre-promotion shape — one
                // drawer for the whole segment, closed by default.
                <div data-testid={`segment-details-${index}`}>
                  <button
                    type="button"
                    onClick={() => setExpanded((prev) => ({ ...prev, [index]: !(prev[index] ?? defaultOpen) }))}
                    className="flex items-center gap-1 text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                  >
                    <ChevronRight
                      className={cn('h-3 w-3 transition-transform', open && 'rotate-90')}
                    />
                    {t('chat.segment.details', 'Reasoning & tools')} ({segment.process.length})
                  </button>
                  {open && <TurnTimeline events={segment.process} defaultOpen />}
                </div>
              )
            )}

            {segment.reply && (
              <div
                data-testid={`segment-reply-${index}`}
                className="markdown-content text-[var(--text-primary)]"
              >
                {fallbackKindFromReplyVia(segment.reply.via) === 'no_reply' && (
                  // Soft / informational: the agent finished thinking but didn't
                  // call the reply tool; helper_llm wrote what it should have.
                  <div
                    className="text-[10px] uppercase tracking-[0.14em] mb-1"
                    style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-silicon)' }}
                    title={t('chat.timeline.helperFallbackTip')}
                  >
                    {t('chat.timeline.helperFallback')}
                  </div>
                )}
                {fallbackKindFromReplyVia(segment.reply.via) === 'after_error' && (
                  // Warning: a step in this turn actually failed. helper_llm
                  // wrote a recovery reply from what completed.
                  <div
                    className="text-[10px] uppercase tracking-[0.14em] mb-1"
                    style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-warning)' }}
                    title={t('chat.timeline.recoveredAfterErrorTip')}
                  >
                    {t('chat.timeline.recoveredAfterError')}
                  </div>
                )}
                {/* While the segment streams, render plain pre-wrap text:
                    re-parsing the whole markdown per delta saturates the
                    main thread and the UI visibly stalls, then the finished
                    reply pops in at once (the same catch ThinkingBlock and
                    the old ReplyBlock documented on 2026-05-12). Markdown
                    renders on settle. */}
                {isStreaming && (segment.reply.streaming || isLast) ? (
                  <div className="whitespace-pre-wrap text-sm leading-relaxed">
                    {segment.reply.content}
                  </div>
                ) : (
                  <Markdown content={segment.reply.content} />
                )}
                {isStreaming && isLast && (
                  <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-[var(--accent-primary)] align-text-bottom" />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
});
