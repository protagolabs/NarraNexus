/**
 * @file_name: TeamActivityConsole.tsx
 * @author:
 * @date: 2026-07-28
 * @description: Team-room activity surfaces — the collapsible console and the
 *   in-timeline activity bubble.
 *
 * A team room runs up to N agents at once, so the single-agent treatment
 * (TurnTimeline + ExecutionPopover filling the pane) does not transfer: six
 * simultaneous timelines would bury the transcript. The information is instead
 * layered, densest last:
 *
 *   L0  a one-line summary bar        — always visible, collapsed by default
 *   L1  one row per non-idle member   — status, phase, tool count, elapsed
 *   L2  that member's step timeline    — per-phase durations, expanded on demand
 *
 * The console auto-expands only when a member is `stalled`: that is the one
 * state the user should not have to go looking for. Everything else stays
 * folded until asked for.
 *
 * The bubble at the bottom of the transcript carries L0+L1 for a single member
 * (that is where the eye already is while waiting) and can expand to L2 in
 * place, so neither surface needs to drive the other.
 */

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, ChevronDown, ChevronRight, Clock, Loader2, Wrench } from 'lucide-react';
import { RingAvatar } from '@/components/nm';
import { cn, formatTime } from '@/lib/utils';
import {
  STATUS_TONES,
  buildTimeline,
  compareActivity,
  elapsedSince,
  formatDuration,
  hasRecentTurn,
  phaseLabelKey,
  summarise,
  toMs,
} from '@/lib/teamActivity';
import type { TeamMemberActivity } from '@/types/teams';

interface NameLookup {
  (agentId: string): string;
}

/** Live phase text for a running/stalled member ("using Read", "thinking", …). */
function usePhaseLabel() {
  const { t } = useTranslation();
  return (phase?: string | null) => {
    const { key, values } = phaseLabelKey(phase);
    return t(key, values);
  };
}

/**
 * L2 — the per-phase step timeline of one turn.
 *
 * Rendered as a rail so a long turn reads as a sequence rather than a wall of
 * rows; the final step of a live turn is marked "in progress" instead of being
 * given a misleading finished duration.
 */
function StepTimeline({
  activity,
  now,
  live,
}: {
  activity: TeamMemberActivity;
  now: number;
  live: boolean;
}) {
  const { t } = useTranslation();
  const phaseLabel = usePhaseLabel();
  const entries = useMemo(
    () => buildTimeline(activity.steps?.items, now, { live, endedAt: activity.finished_at }),
    [activity.steps, activity.finished_at, now, live],
  );

  if (entries.length === 0) {
    return (
      <div className="px-2 py-1.5 text-[11px] text-[var(--text-tertiary)]">
        {t('chat.team.activity.noSteps')}
      </div>
    );
  }

  return (
    <div className="mt-1 space-y-0.5">
      {(activity.steps?.dropped ?? 0) > 0 && (
        <div className="pl-4 text-[10px] text-[var(--text-tertiary)]">
          {t('chat.team.activity.stepsDropped', { n: activity.steps?.dropped ?? 0 })}
        </div>
      )}
      {entries.map((entry, i) => (
        <div key={`${entry.at}-${i}`} className="flex items-baseline gap-2 pl-1 text-[11px]">
          <span
            className={cn(
              'mt-[3px] h-1.5 w-1.5 shrink-0 self-start rounded-full',
              entry.ongoing && 'animate-pulse',
            )}
            style={{
              background: entry.ongoing ? 'var(--color-silicon)' : 'var(--nm-subtle)',
            }}
          />
          <span className="font-mono tabular-nums text-[10px] text-[var(--text-tertiary)]">
            {toMs(entry.at) === null ? '' : formatTime(entry.at)}
          </span>
          <span className="min-w-0 flex-1 truncate text-[var(--nm-ink)]">
            {phaseLabel(entry.phase)}
          </span>
          <span className="shrink-0 font-mono tabular-nums text-[10px] text-[var(--text-tertiary)]">
            {entry.ongoing
              ? t('chat.team.activity.stepOngoing', { duration: formatDuration(entry.durationMs) })
              : formatDuration(entry.durationMs)}
          </span>
        </div>
      ))}
    </div>
  );
}

/** The right-hand metric of an L1 row: elapsed, wait time, or silence. */
function RowMetric({ activity, now }: { activity: TeamMemberActivity; now: number }) {
  const { t } = useTranslation();
  if (activity.status === 'running') {
    return <>{elapsedSince(activity.started_at, now)}</>;
  }
  if (activity.status === 'stalled') {
    return <>{t('chat.team.activity.silentFor', { duration: elapsedSince(activity.last_signal_at, now) })}</>;
  }
  if (activity.status === 'queued') {
    return <>{t('chat.team.activity.waitingFor', { duration: elapsedSince(activity.queued_since, now) })}</>;
  }
  if (activity.finished_at) {
    return <>{t('chat.team.activity.finishedAgo', { duration: elapsedSince(activity.finished_at, now) })}</>;
  }
  return null;
}

/** L1 — one member's row, expandable to its step timeline. */
function MemberRow({
  activity,
  name,
  now,
  expanded,
  onToggle,
}: {
  activity: TeamMemberActivity;
  name: string;
  now: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const phaseLabel = usePhaseLabel();
  const tone = STATUS_TONES[activity.status];
  const live = activity.status === 'running';
  const hasDetail = (activity.steps?.items?.length ?? 0) > 0;

  const primary =
    activity.status === 'running' || activity.status === 'stalled'
      ? phaseLabel(activity.phase)
      : t(tone.labelKey);

  return (
    <div className="rounded-[var(--radius-md)] border border-transparent hover:border-[var(--rule)]">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
      >
        {activity.status === 'stalled' ? (
          <AlertTriangle className="h-3 w-3 shrink-0" style={{ color: tone.color }} />
        ) : (
          <span
            className={cn('h-1.5 w-1.5 shrink-0 rounded-full', live && 'animate-pulse')}
            style={{ background: tone.color }}
          />
        )}
        <span className="min-w-0 max-w-[40%] truncate text-xs text-[var(--nm-ink)]">{name}</span>
        <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
          {primary}
        </span>
        {(activity.tool_count ?? 0) > 0 && (
          <span className="flex shrink-0 items-center gap-1 font-mono text-[10px] text-[var(--text-tertiary)]">
            <Wrench className="h-2.5 w-2.5" />
            {activity.tool_count}
          </span>
        )}
        {(activity.queued_count ?? 0) > 1 && (
          <span className="shrink-0 font-mono text-[10px] text-[var(--text-tertiary)]">
            ×{activity.queued_count}
          </span>
        )}
        <span className="shrink-0 font-mono tabular-nums text-[10px] text-[var(--text-tertiary)]">
          <RowMetric activity={activity} now={now} />
        </span>
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-[var(--text-tertiary)]" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-[var(--text-tertiary)]" />
        )}
      </button>

      {expanded && (
        <div className="border-t border-[var(--rule)] px-2 py-1.5">
          <p className="text-[11px] leading-relaxed text-[var(--text-secondary)]">
            {t(tone.hintKey)}
          </p>
          {hasDetail && <StepTimeline activity={activity} now={now} live={live} />}
          {(activity.status === 'running' || activity.status === 'stalled') && activity.started_at && (
            <div className="mt-1.5 font-mono text-[10px] text-[var(--text-tertiary)]">
              {t('chat.team.activity.startedAt', { time: formatTime(activity.started_at) })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * L0 + L1 — the room's activity console.
 *
 * Renders nothing at all when the room is completely quiet: an empty panel is
 * chrome, not information.
 */
export function TeamActivityConsole({
  activity,
  nameOf,
  now,
}: {
  activity: TeamMemberActivity[];
  nameOf: NameLookup;
  now: number;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const summary = useMemo(() => summarise(activity, now), [activity, now]);
  const rows = useMemo(
    () =>
      activity
        .filter((a) => a.status !== 'idle' || hasRecentTurn(a, now))
        .sort((a, b) => compareActivity(a, b, nameOf)),
    [activity, nameOf, now],
  );

  if (rows.length === 0) return null;

  // A stalled member is surfaced without being asked for; everything else
  // respects the user's fold.
  const expanded = open || summary.needsAttention;

  const parts: string[] = [];
  if (summary.running) parts.push(t('chat.team.activity.countWorking', { n: summary.running }));
  if (summary.queued) parts.push(t('chat.team.activity.countWaiting', { n: summary.queued }));
  if (summary.stalled) parts.push(t('chat.team.activity.countStalled', { n: summary.stalled }));

  return (
    <div className="shrink-0 border-b border-[var(--rule)] bg-[var(--bg-secondary)]/40 px-3 py-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 text-left"
      >
        {summary.stalled > 0 ? (
          <AlertTriangle className="h-3 w-3 shrink-0" style={{ color: STATUS_TONES.stalled.color }} />
        ) : summary.running > 0 ? (
          <Loader2 className="h-3 w-3 shrink-0 animate-spin text-[var(--color-silicon)]" />
        ) : (
          <Clock className="h-3 w-3 shrink-0 text-[var(--text-tertiary)]" />
        )}
        <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--text-secondary)]">
          {parts.length > 0 ? parts.join(' · ') : t('chat.team.activity.recentlyFinished')}
        </span>
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-[var(--text-tertiary)]" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-[var(--text-tertiary)]" />
        )}
      </button>

      {expanded && (
        <div className="mt-1 space-y-0.5">
          {rows.map((a) => (
            <MemberRow
              key={a.agent_id}
              activity={a}
              name={nameOf(a.agent_id)}
              now={now}
              expanded={expandedId === a.agent_id}
              onToggle={() => setExpandedId((cur) => (cur === a.agent_id ? null : a.agent_id))}
            />
          ))}
          {summary.idle > 0 && (
            <div className="px-2 pt-0.5 text-[10px] text-[var(--text-tertiary)]">
              {t('chat.team.activity.countIdle', { n: summary.idle })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The in-transcript activity bubble for ONE member.
 *
 * Shaped like an agent message bubble so a member that is about to speak
 * occupies the place its reply will land — and carries enough (phase, tool
 * count, elapsed) that the console is an option, not a necessity.
 */
export function TeamActivityBubble({
  activity,
  name,
  now,
}: {
  activity: TeamMemberActivity;
  name: string;
  now: number;
}) {
  const { t } = useTranslation();
  const phaseLabel = usePhaseLabel();
  const [open, setOpen] = useState(false);
  const tone = STATUS_TONES[activity.status];
  const live = activity.status === 'running';
  const hasDetail = (activity.steps?.items?.length ?? 0) > 0;

  const primary =
    live || activity.status === 'stalled' ? phaseLabel(activity.phase) : t(tone.labelKey);

  return (
    <div className="flex gap-3">
      <RingAvatar
        species="silicon"
        label={name.slice(0, 2)}
        size="sm"
        className="hidden shrink-0 md:inline-flex"
      />
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 px-0.5 font-mono text-[10px] text-[var(--text-tertiary)]">{name}</div>
        <div
          className="nm-bubble-ai relative inline-block max-w-[85%] rounded-[var(--radius-lg)] px-3.5 py-2.5"
          style={{
            background: 'var(--color-silicon-soft)',
            border: '1px solid var(--color-silicon-hair)',
            borderLeft: `3px solid ${tone.color}`,
          }}
        >
          <button
            type="button"
            onClick={() => hasDetail && setOpen((v) => !v)}
            aria-expanded={hasDetail ? open : undefined}
            aria-label={primary}
            className={cn('flex items-center gap-2', !hasDetail && 'cursor-default')}
          >
            {live ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-silicon)]" />
            ) : activity.status === 'stalled' ? (
              <AlertTriangle className="h-3.5 w-3.5" style={{ color: tone.color }} />
            ) : (
              <span className="flex gap-1">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="h-1.5 w-1.5 animate-bounce rounded-full"
                    style={{ background: tone.color, animationDelay: `${i * 0.15}s` }}
                  />
                ))}
              </span>
            )}
            <span className="text-xs text-[var(--nm-ink)]">{primary}</span>
            {(activity.tool_count ?? 0) > 0 && (
              <span className="flex items-center gap-0.5 font-mono text-[10px] text-[var(--text-tertiary)]">
                <Wrench className="h-2.5 w-2.5" />
                {activity.tool_count}
              </span>
            )}
            <span className="font-mono tabular-nums text-[10px] text-[var(--text-tertiary)]">
              <RowMetric activity={activity} now={now} />
            </span>
            {hasDetail &&
              (open ? (
                <ChevronDown className="h-3 w-3 text-[var(--text-tertiary)]" />
              ) : (
                <ChevronRight className="h-3 w-3 text-[var(--text-tertiary)]" />
              ))}
          </button>

          {open && hasDetail && (
            <div className="mt-1.5 border-t border-[var(--color-silicon-hair)] pt-1.5">
              <p className="mb-1 text-[11px] leading-relaxed text-[var(--text-secondary)]">
                {t(tone.hintKey)}
              </p>
              <StepTimeline activity={activity} now={now} live={live} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
