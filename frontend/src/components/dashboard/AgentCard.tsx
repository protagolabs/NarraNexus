/**
 * @file_name: AgentCard.tsx
 * @description: v2.1.2 — agent card with two-tier visibility (collapsed vs
 * expanded) driven by card-body click (inner buttons stopPropagation).
 *
 * Changes vs v2.1.1:
 *   - Card body itself is clickable again (regression fix). Inner interactive
 *     sections (banners, section headers, items, action buttons) all call
 *     e.stopPropagation() in their own handlers, so clicking a session row
 *     or a Retry button doesn't bubble up and toggle the whole card.
 *   - When all attention banners are dismissed in sessionStorage, the status
 *     rail dims (opacity-40) instead of staying bright red/amber. The
 *     underlying health is still "error" or "warning" (server-driven), but
 *     the visual urgency drops once the user has acknowledged. If count
 *     changes (new failure), banners re-appear and the rail un-dims.
 *
 * Layout (owned agents):
 *   COLLAPSED (default):
 *     Header · verb_line · banners · inline queue+metrics + ▾ more hint
 *   EXPANDED:
 *     above + sessions + jobs + sparkline + recent feed
 */
import type { AgentStatus, OwnedAgentStatus, AttentionBanner } from '@/types';
import { StatusBadge } from './StatusBadge';
import { DurationDisplay } from './DurationDisplay';
import { ConcurrencyBadge } from './ConcurrencyBadge';
import { AttentionBanners } from './AttentionBanners';
import { SessionSection } from './SessionSection';
import { JobsSection } from './JobsSection';
import { QueueBar } from './QueueBar';
import { Sparkline } from './Sparkline';
import { RecentFeed } from './RecentFeed';
import { MetricsRow } from './MetricsRow';
import { HEALTH_COLORS, acknowledgedHealthOf } from './healthColors';
import { useAllBannersDismissed, bannerKey } from './expandState';

const HEALTH_TOOLTIP = {
  healthy_running: 'Healthy · running',
  healthy_idle: 'Healthy · idle (recently active)',
  idle_long: 'Quiet · idle > 72h',
  warning: 'Warning · job blocked',
  paused: 'Paused · jobs paused by user',
  error: 'Error · failed job or error event',
  acknowledged: 'Error acknowledged · underlying issue still present',
} as const;

interface Props {
  agent: AgentStatus;
  onToggleExpand: () => void;
  expanded?: boolean;
}

export function AgentCard({ agent, onToggleExpand, expanded }: Props) {
  if (!agent.owned_by_viewer) {
    return <PublicCard agent={agent} />;
  }
  return <OwnedCard agent={agent} expanded={!!expanded} onToggleExpand={onToggleExpand} />;
}

function PublicCard({ agent }: { agent: AgentStatus }) {
  const colors = HEALTH_COLORS.healthy_idle;
  return (
    <div
      data-testid={`agent-card-${agent.agent_id}`}
      className="group flex overflow-hidden rounded-xl border border-[var(--border-primary)] bg-[var(--bg-glass)]"
    >
      <div className={`w-1 shrink-0 ${colors.rail}`} aria-hidden />
      <div className="flex-1 p-3 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="truncate font-semibold text-sm">{agent.name}</span>
            <StatusBadge kind={agent.status.kind} />
            <ConcurrencyBadge agent={agent} />
          </div>
          <DurationDisplay startedAt={agent.status.started_at} />
        </div>
        {agent.description && (
          <div className="mt-1 text-xs text-[var(--text-secondary)] italic truncate">
            {agent.description}
          </div>
        )}
      </div>
    </div>
  );
}

function OwnedCard({
  agent,
  expanded,
  onToggleExpand,
}: {
  agent: OwnedAgentStatus;
  expanded: boolean;
  onToggleExpand: () => void;
}) {
  const banners = agent.attention_banners ?? [];
  const staleInstances = agent.stale_instances ?? [];
  const allKeys = banners.map((b: AttentionBanner) =>
    bannerKey(agent.agent_id, b.kind, b.message),
  );
  const allDismissed = useAllBannersDismissed(allKeys);

  // v2.2 G2: derive what to render. error NEVER downgrades to healthy
  // (Security-M1) — it becomes 'acknowledged' (slate rail + red dot).
  const effectiveHealth = acknowledgedHealthOf(
    agent.health,
    allDismissed,
    agent.status.kind,
  );
  const colors = HEALTH_COLORS[effectiveHealth];
  const isAcknowledgedError = effectiveHealth === 'acknowledged';

  const verbLine = agent.verb_line;
  const hasSessions = agent.sessions.length > 0;
  const hasJobs = agent.running_jobs.length > 0 || agent.pending_jobs.length > 0;
  const hasRecent = agent.recent_events.length > 0;

  return (
    <div
      data-testid={`agent-card-${agent.agent_id}`}
      data-expanded={expanded ? 'true' : 'false'}
      data-health={effectiveHealth}
      data-server-health={agent.health}
      data-banners-dismissed={allDismissed ? 'true' : 'false'}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={onToggleExpand}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onToggleExpand();
        }
      }}
      className={`group relative flex overflow-hidden rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-glass)] backdrop-blur-sm shadow-sm hover:shadow-lg hover:border-[var(--accent-primary)]/60 transition-all duration-200 cursor-pointer ${colors.cardTint} ${agent.health === 'idle_long' ? 'opacity-75' : ''}`}
    >
      {/* Status rail with optional ack-dot (v2.2 G2 + G4) */}
      <div className="relative shrink-0">
        <div
          className={`w-1 h-full ${colors.rail} transition-colors duration-200`}
          title={HEALTH_TOOLTIP[effectiveHealth as keyof typeof HEALTH_TOOLTIP]}
          aria-hidden
        />
        {isAcknowledgedError && (
          <span
            data-testid="ack-dot"
            title="Error acknowledged — underlying issue still present"
            aria-label="error acknowledged"
            className="pointer-events-none absolute -right-[3px] top-2 h-2 w-2 rounded-full bg-red-500 ring-2 ring-[var(--bg-glass)]"
          />
        )}
      </div>

      <div className="flex-1 p-3.5 min-w-0">
        {/* Header — name + kind pill + stale badge + duration */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="truncate text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
              {agent.name}
            </span>
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium bg-[var(--bg-tertiary,rgba(127,127,127,0.08))] ${colors.text}`}
            >
              <StatusBadge kind={agent.status.kind} />
            </span>
            {staleInstances.length > 0 && (
              <span
                data-testid="stale-badge"
                title={`${staleInstances.length} zombie instance(s): ${staleInstances.map((s) => s.module_class).join(', ')}`}
                className="inline-flex items-center gap-1 rounded-full bg-gray-500/10 px-2 py-0.5 text-[10px] font-medium text-gray-600 dark:text-gray-400"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-gray-500" aria-hidden />
                {staleInstances.length} stale
              </span>
            )}
          </div>
          <DurationDisplay startedAt={agent.status.started_at} />
        </div>

        {/* Verb line (always — primary narrative) */}
        {verbLine && (
          <div
            className={`mt-1.5 text-[13px] leading-snug ${colors.text}`}
            data-testid="verb-line"
          >
            {verbLine}
          </div>
        )}

        {/* Banners (each dismissible individually) */}
        <AttentionBanners agentId={agent.agent_id} banners={banners} />

        {/* Inline summary row — visible in both collapsed + expanded modes */}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 tabular-nums">
          <QueueBar queue={agent.queue} compact />
          <MetricsRow metrics={agent.metrics_today} />
          <span className="ml-auto text-[11px] text-[var(--text-secondary)] group-hover:text-[var(--accent-primary)] group-hover:translate-y-px transition-all duration-150">
            {expanded ? '▴ less' : '▾ more'}
          </span>
        </div>

        {/* Expanded sections — grid 0fr→1fr animation (v2.2 G4) */}
        <div
          className={`grid transition-[grid-template-rows] duration-200 ease-out ${expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}
          aria-hidden={!expanded}
        >
          <div className="overflow-hidden">
            <div className="mt-3 space-y-2 border-t border-[var(--border-primary)]/50 pt-3">
              {hasSessions && (
                <SessionSection agentId={agent.agent_id} sessions={agent.sessions} />
              )}
              {hasJobs && (
                <JobsSection
                  agentId={agent.agent_id}
                  runningJobs={agent.running_jobs}
                  pendingJobs={agent.pending_jobs}
                />
              )}
              <Sparkline agentId={agent.agent_id} health={agent.health} />
              {hasRecent && (
                <RecentFeed agentId={agent.agent_id} events={agent.recent_events} />
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
