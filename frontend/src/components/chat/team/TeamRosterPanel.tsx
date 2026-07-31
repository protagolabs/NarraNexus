/**
 * @file_name: TeamRosterPanel.tsx
 * @author:
 * @date: 2026-07-30
 * @description: The team room's standing member column — every member, its
 *   status, its timer, and one click to that member's process.
 *
 * A folded console answers "is anything happening" only after you ask it. In a
 * room where six agents work in parallel, the question the user actually holds
 * is "what is EACH of them doing, right now" — so the roster is permanent
 * chrome down the right edge: one row per member, always, including members the
 * activity poll never mentioned (absent from `activity` = idle with no trace).
 *
 * Detail comes from two different sources, and the split is not cosmetic:
 *   - running / stalled → the phase timeline the poll already carries. The
 *     turn's event_log row does not exist yet; it is written when the turn ends.
 *   - idle → that member's persisted event log, fetched once per turn through
 *     the existing endpoint, rendered with the same terminal rows the
 *     single-agent ProcessPanel uses.
 *
 * Expansion is CONTROLLED (`expandedId` + `onToggle`): the parent needs the
 * same id to drive the transcript's typing bubble, and two components owning
 * one selection is how they drift apart.
 */

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { cn, formatTime } from '@/lib/utils';
import {
  STATUS_TONES,
  buildTimeline,
  compareActivity,
  elapsedSince,
  formatDuration,
  lastRunSummary,
  phaseLabelKey,
  toMs,
} from '@/lib/teamActivity';
import { friendlyToolName } from '../process/processShared';
import { TurnTimeline } from '../TurnTimeline';
import { isProcessEvent, useTurnDetail } from './useTurnDetail';
import type { AgentInfo } from '@/types';
import type { TeamMemberActivity } from '@/types/teams';

export interface TeamRosterPanelProps {
  /** Every member of the room; only `agent_id` / `name` are consumed. */
  members: AgentInfo[];
  /** Polled activity. A member missing here is idle with no trace. */
  activity: TeamMemberActivity[];
  leadAgentId: string | null;
  /** One clock for the whole panel (1s tick, epoch ms) — driven by the parent
   *  so no two durations on screen disagree by a tick. */
  now: number;
  /** The member whose detail is open; null = all collapsed. */
  expandedId: string | null;
  /** Row click. The parent owns same-id → null (collapse). */
  onToggle: (agentId: string) => void;
  /** Empty-state escape hatch to the team's settings page. */
  onOpenSettings?: () => void;
  /** Shell override — the narrow-screen drawer reuses the same rows. */
  className?: string;
}

/**
 * The live turn's phases with per-step durations.
 *
 * The final step of a running turn is marked "ongoing" rather than given a
 * duration that implies it finished — a step that has been open for 12s and a
 * step that took 12s read identically otherwise.
 */
function PhaseTimeline({
  activity,
  now,
  live,
}: {
  activity: TeamMemberActivity;
  now: number;
  live: boolean;
}) {
  const { t } = useTranslation();
  const entries = useMemo(
    () => buildTimeline(activity.steps?.items, now, { live, endedAt: activity.finished_at }),
    [activity.steps, activity.finished_at, now, live],
  );

  return (
    <div className="space-y-0.5">
      {(activity.steps?.dropped ?? 0) > 0 && (
        <div className="pl-4 text-[10px] text-[var(--text-tertiary)]">
          {t('chat.team.activity.stepsDropped', { n: activity.steps?.dropped ?? 0 })}
        </div>
      )}
      {entries.map((entry, i) => {
        const { key, values } = phaseLabelKey(entry.phase);
        return (
          <div key={`${entry.at}-${i}`} className="flex items-baseline gap-2 pl-1">
            <span
              className={cn(
                'mt-[3px] h-1.5 w-1.5 shrink-0 self-start rounded-full',
                entry.ongoing && 'animate-pulse',
              )}
              style={{ background: entry.ongoing ? 'var(--color-silicon)' : 'var(--nm-subtle)' }}
            />
            <span className="tabular-nums text-[10px] text-[var(--text-tertiary)]">
              {toMs(entry.at) === null ? '' : formatTime(entry.at)}
            </span>
            <span className="min-w-0 flex-1 truncate text-[var(--nm-ink)]">{t(key, values)}</span>
            <span className="shrink-0 tabular-nums text-[10px] text-[var(--text-tertiary)]">
              {entry.ongoing
                ? t('chat.team.activity.stepOngoing', { duration: formatDuration(entry.durationMs) })
                : formatDuration(entry.durationMs)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** The row's right-hand metric: elapsed, wait, silence, or the last run. */
function RowMetric({ activity, now }: { activity: TeamMemberActivity; now: number }) {
  const { t } = useTranslation();

  if (activity.status === 'running') return <>{elapsedSince(activity.started_at, now)}</>;
  if (activity.status === 'stalled') {
    return (
      <>{t('chat.team.activity.silentFor', { duration: elapsedSince(activity.last_signal_at, now) })}</>
    );
  }
  if (activity.status === 'queued') {
    return (
      <>
        {t('chat.team.activity.waitingFor', { duration: elapsedSince(activity.queued_since, now) })}
        {(activity.queued_count ?? 0) > 1 && ` ×${activity.queued_count}`}
      </>
    );
  }

  const last = lastRunSummary(activity, now);
  if (!last) return <>{t('chat.team.roster.neverRan')}</>;
  // Legacy rows without started_at have no honest duration — say when it
  // finished rather than invent a "ran 0s".
  if (last.durationMs === null) {
    return <>{t('chat.team.roster.lastRunAgoOnly', { ago: formatDuration(last.agoMs) })}</>;
  }
  return (
    <>
      {t('chat.team.roster.lastRun', {
        duration: formatDuration(last.durationMs),
        ago: formatDuration(last.agoMs),
      })}
    </>
  );
}

/** What a running / stalled member is doing at this instant. */
function CurrentAction({ phase }: { phase?: string | null }) {
  const { t } = useTranslation();
  if (phase?.startsWith('tool:')) {
    return (
      <span className="flex min-w-0 items-center gap-1 text-[11px]">
        <span aria-hidden="true" className="shrink-0 select-none font-semibold" style={{ color: 'var(--color-success)' }}>
          $
        </span>
        <span className="truncate font-mono" style={{ color: 'var(--color-silicon)' }}>
          {friendlyToolName(phase.slice(5))}
        </span>
      </span>
    );
  }
  const { key, values } = phaseLabelKey(phase);
  return <span className="truncate text-[11px] text-[var(--text-secondary)]">{t(key, values)}</span>;
}

/** The expanded body — live phases while running, persisted process once idle. */
function MemberDetail({
  activity,
  now,
  open,
}: {
  activity: TeamMemberActivity;
  now: number;
  open: boolean;
}) {
  const { t } = useTranslation();
  const live = activity.status === 'running' || activity.status === 'stalled';
  const detail = useTurnDetail(activity.agent_id, live ? null : activity.event_id, open);

  if (!open) return null;

  // A result from the PREVIOUS turn must not be shown under the current one:
  // only a state whose key still matches counts as settled.
  const settled =
    detail && detail.key === `${activity.agent_id}:${activity.event_id}` ? detail : null;

  let body: React.ReactNode;
  if (live) {
    body = (activity.steps?.items?.length ?? 0) > 0 ? (
      <PhaseTimeline activity={activity} now={now} live={activity.status === 'running'} />
    ) : (
      <span className="text-[var(--text-tertiary)]">
        {t('chat.execution.startingUp', 'Starting up…')}
      </span>
    );
  } else if (!activity.event_id) {
    body = <span className="text-[var(--text-tertiary)]">{t('chat.team.roster.noProcess')}</span>;
  } else if (!settled) {
    body = (
      <span className="flex items-center gap-2 text-[var(--text-tertiary)]">
        <Loader2 className="h-3 w-3 shrink-0 animate-spin" style={{ color: 'var(--color-silicon)' }} />
        {t('chat.team.roster.loading')}
      </span>
    );
  } else if (settled.kind === 'ready') {
    // Same renderer as the single-chat "View reasoning & tools" disclosure —
    // THINKING blocks in Markdown, expandable tool args — not the compact
    // one-line ProcessPanel rail (user feedback 2026-07-31).
    body = <TurnTimeline events={settled.events.filter(isProcessEvent)} />;
  } else {
    body = <span className="text-[var(--text-tertiary)]">{t('chat.team.roster.noProcess')}</span>;
  }

  return (
    <div className="max-h-[45vh] overflow-y-auto border-t border-[var(--rule)] px-2 py-1.5 font-mono text-xs">
      {body}
    </div>
  );
}

/** One member: status, name, metric, live action — and its detail underneath. */
function MemberRow({
  activity,
  name,
  isLead,
  now,
  expanded,
  onToggle,
}: {
  activity: TeamMemberActivity;
  name: string;
  isLead: boolean;
  now: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const tone = STATUS_TONES[activity.status];
  const stalled = activity.status === 'stalled';
  const showsAction = stalled || activity.status === 'running';

  return (
    <div
      className={cn(
        'border-b border-[var(--rule)]',
        // A wedged worker gets a tinted row: the one state the user should not
        // have to go looking for. (An amber wash off the existing token — there
        // is no `--color-warning-soft`, and inventing one is 铁律 #1.)
        stalled && 'bg-[var(--color-warning)]/5',
      )}
    >
      <button
        type="button"
        data-testid={`roster-row-${activity.agent_id}`}
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full flex-col gap-0.5 px-2.5 py-2 text-left hover:bg-[var(--nm-paper-warm)]"
      >
        <span className="flex w-full items-center gap-1.5">
          {stalled ? (
            <AlertTriangle className="h-3 w-3 shrink-0" style={{ color: tone.color }} />
          ) : (
            <span
              className={cn(
                'h-1.5 w-1.5 shrink-0 rounded-full',
                activity.status === 'running' && 'animate-pulse',
              )}
              style={{ background: tone.color }}
            />
          )}
          <span className="min-w-0 flex-1 truncate text-xs text-[var(--nm-ink)]">{name}</span>
          {isLead && (
            <span
              title={t('chat.team.leadTitle', { name })}
              className="h-[2px] w-[2px] shrink-0 rounded-full ring-1 ring-[var(--color-carbon)]"
            />
          )}
        </span>
        <span className="flex w-full items-center gap-1.5">
          {showsAction ? <CurrentAction phase={activity.phase} /> : <span className="flex-1" />}
          <span className="ml-auto shrink-0 font-mono tabular-nums text-[10px] text-[var(--text-tertiary)]">
            <RowMetric activity={activity} now={now} />
          </span>
        </span>
      </button>

      <MemberDetail activity={activity} now={now} open={expanded} />
    </div>
  );
}

export function TeamRosterPanel({
  members,
  activity,
  leadAgentId,
  now,
  expandedId,
  onToggle,
  onOpenSettings,
  className,
}: TeamRosterPanelProps) {
  const { t } = useTranslation();

  const nameOf = useMemo(() => {
    const names = new Map(members.map((m) => [m.agent_id, m.name || m.agent_id]));
    return (agentId: string) => names.get(agentId) ?? agentId;
  }, [members]);

  // Members drive the list, activity only decorates it: a member the poll has
  // never seen is idle with no trace, not an absent teammate.
  const rows = useMemo(() => {
    const byId = new Map(activity.map((a) => [a.agent_id, a]));
    return members
      .map<TeamMemberActivity>(
        (m) => byId.get(m.agent_id) ?? { agent_id: m.agent_id, status: 'idle' },
      )
      .sort((a, b) => compareActivity(a, b, nameOf));
  }, [members, activity, nameOf]);

  return (
    <aside
      className={cn(
        'flex w-60 shrink-0 flex-col border-l border-[var(--rule)] min-h-0',
        className,
      )}
    >
      <div className="flex shrink-0 items-center gap-1.5 border-b border-[var(--rule)] px-2.5 py-2 font-mono text-[10px] uppercase tracking-wider text-[var(--text-tertiary)]">
        <span>{t('chat.team.roster.title')}</span>
        <span className="tabular-nums">{members.length}</span>
      </div>

      {members.length === 0 ? (
        <div className="flex flex-1 flex-col items-start gap-2 px-2.5 py-3">
          <p className="text-xs leading-relaxed text-[var(--text-tertiary)]">
            {t('chat.team.noAgents')}
          </p>
          {onOpenSettings && (
            <button
              type="button"
              onClick={onOpenSettings}
              className="rounded-[var(--radius-xs)] border border-[var(--rule)] px-2 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--color-carbon)]"
            >
              {t('chat.team.teamSettings')}
            </button>
          )}
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          {rows.map((a) => (
            <MemberRow
              key={a.agent_id}
              activity={a}
              name={nameOf(a.agent_id)}
              isLead={a.agent_id === leadAgentId}
              now={now}
              expanded={expandedId === a.agent_id}
              onToggle={() => onToggle(a.agent_id)}
            />
          ))}
        </div>
      )}
    </aside>
  );
}
