/**
 * @file_name: AgentGroupSection.tsx
 * @author:
 * @date: 2026-06-10
 * @description: Renders one collapsible team section inside the grouped
 * agent list. Owns the section header (team name, member count, collapse
 * toggle, aggregated unread pill) and the agent rows beneath it.
 * Rows are display-only — every agent mutation now lives on the agent
 * profile page, so the row carries no per-row action affordance.
 */

import { useTranslation } from 'react-i18next';
import { Loader2, Globe, ChevronRight } from 'lucide-react';
import type { AgentInfo } from '@/types';
import { RingAvatar } from '@/components/nm';
import { aggregateSectionUnread } from './agentGroupUtils';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RowMeta {
  preview: string;
  time: string;
  unread: number;
}

export interface AgentGroupSectionProps {
  teamId: string | null;
  teamName: string;
  teamColor: string | null;

  agents: AgentInfo[];
  agentId: string | null;
  /** The team whose group chat is currently open (route /app/teams/:id/chat).
   *  When set, this team's Group chat row is highlighted and NO agent row is. */
  activeTeamChatId?: string | null;

  /** Whether the section is collapsed (agent rows hidden). */
  collapsed: boolean;
  onToggleCollapse: (teamId: string | null) => void;

  /**
   * Render agent rows without the section header. Used by the pure
   * no-teams scenario, where a single "Ungrouped" header is noise.
   */
  hideHeader?: boolean;

  onSelectAgent: (agentId: string) => void;

  getRowMeta: (agentId: string) => RowMeta;
  getIsStreaming: (agentId: string) => boolean;
  completedAgentIds: string[];

  /** Logged-in user — the read-only Globe badge marks OTHER users' public
   *  agents, so the row still needs to know who is looking. */
  currentUserId: string | null;
}

// ---------------------------------------------------------------------------
// AgentGroupSection
// ---------------------------------------------------------------------------

/**
 * One collapsible section in the grouped sidebar.
 *
 * The section header always renders; agent rows are conditionally hidden
 * when collapsed=true. An aggregated unread pill appears in the header
 * when the section is collapsed and the total unread > 0, so the user
 * can see there is activity without expanding.
 *
 * Ungrouped section (teamId=null) renders a hollow dot instead of a
 * filled color swatch to visually distinguish it from named teams.
 */
export function AgentGroupSection({
  teamId,
  teamName,
  agents,
  agentId,
  activeTeamChatId,
  collapsed,
  onToggleCollapse,
  hideHeader = false,
  onSelectAgent,
  getRowMeta,
  getIsStreaming,
  completedAgentIds,
  currentUserId,
}: AgentGroupSectionProps) {
  const totalUnread = aggregateSectionUnread(agents, (aid) => getRowMeta(aid).unread);

  // When a team's group chat is the active view, no agent row should look
  // selected (avoids the confusing "an agent is highlighted while I'm in the
  // group chat" state). Teams themselves live in the separate TEAMS section.
  const effectiveAgentId = activeTeamChatId ? null : agentId;

  return (
    <div className="group/section relative">
      {/* Section header — clicking the team name opens its group chat; clicking
          anywhere else on the row toggles the section's collapse. Rendered as a
          div (not a button) so the name can be a nested button. */}
      {!hideHeader && (
      <div
        role="button"
        tabIndex={0}
        aria-label={teamName}
        data-help-id="sidebar.team-section"
        onClick={() => onToggleCollapse(teamId)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggleCollapse(teamId);
          }
        }}
        className={cn(
          'w-full flex items-center gap-2 px-3 py-1.5 cursor-pointer',
          'text-left transition-colors hover:bg-[var(--nm-paper-warm)]',
        )}
      >
        {/* The Ungrouped pseudo-section keeps a plain hollow dot; named teams
            carry their group-chat avatar on the dedicated row below. */}
        {teamId === null && (
          <span
            className="w-2 h-2 rounded-full shrink-0 border"
            style={{ borderColor: 'var(--nm-ink30)' }}
          />
        )}

        {/* Section label */}
        <span
          className="flex-1 min-w-0 text-[11px] font-mono uppercase tracking-wider truncate"
          style={{ color: 'var(--nm-ink50)' }}
        >
          {teamName}
        </span>

        {/* Member count */}
        <span
          className="text-[10px] font-mono shrink-0"
          style={{ color: 'var(--nm-ink30)' }}
        >
          {agents.length}
        </span>

        {/* Aggregated unread pill — visible only when section is collapsed
            and there is at least one unread message. */}
        {collapsed && totalUnread > 0 && (
          <span
            className="inline-flex items-center justify-center text-[10px] font-semibold shrink-0"
            style={{
              minWidth: 18,
              height: 16,
              padding: '0 5px',
              borderRadius: 8,
              background: 'transparent',
              border: '1px solid var(--nm-ink30)',
              color: 'var(--nm-ink70)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {totalUnread > 99 ? '99+' : totalUnread}
          </span>
        )}

        {/* Collapse arrow — lucide chevron, same linear icon language as the
            team-row toggle (design_system.md §5: no solid/linear mixing). */}
        <ChevronRight
          className={cn(
            'h-3 w-3 shrink-0 transition-transform duration-150',
            !collapsed && 'rotate-90',
          )}
          style={{ color: 'var(--nm-ink30)' }}
          aria-hidden
        />
      </div>
      )}

      {/* Agent rows — a headerless section cannot be collapsed */}
      {(hideHeader || !collapsed) && (
        <div className="space-y-0.5 px-1 pb-1">
          {agents.map((agent, index) => (
            <AgentRow
              key={agent.agent_id}
              agent={agent}
              activeAgentId={effectiveAgentId}
              index={index}
              getRowMeta={getRowMeta}
              getIsStreaming={getIsStreaming}
              completedAgentIds={completedAgentIds}
              currentUserId={currentUserId}
              onSelectAgent={onSelectAgent}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AgentRow (private to this file)
// ---------------------------------------------------------------------------

interface AgentRowProps {
  agent: AgentInfo;
  activeAgentId: string | null;
  index: number;
  getRowMeta: (aid: string) => RowMeta;
  getIsStreaming: (aid: string) => boolean;
  completedAgentIds: string[];
  currentUserId: string | null;
  onSelectAgent: (agentId: string) => void;
}

/** Single agent row — mirrors the AgentList row but scoped to the group context. */
function AgentRow({
  agent,
  activeAgentId,
  index,
  getRowMeta,
  getIsStreaming,
  completedAgentIds,
  currentUserId,
  onSelectAgent,
}: AgentRowProps) {
  const { t } = useTranslation();
  const isSelected = activeAgentId === agent.agent_id;
  const completed = completedAgentIds.includes(agent.agent_id);
  // Compact single-line rows: no chat preview (it conflated group-chat content
  // anyway) — just name + time + unread, so more agents fit on screen.
  const { time, unread } = getRowMeta(agent.agent_id);
  const streaming = getIsStreaming(agent.agent_id) || !!agent.active_run;
  const displayName = agent.name || agent.agent_id;

  const rowBg = isSelected
    ? 'var(--nm-row-active)'
    : unread > 0
      ? 'var(--color-silicon-soft)'
      : 'transparent';
  const allowHover = !isSelected && unread === 0;

  const isOwner = agent.created_by === currentUserId;

  return (
    <div
      onClick={() => onSelectAgent(agent.agent_id)}
      className={cn(
        'w-full text-left px-3 py-1.5 cursor-pointer animate-slide-up',
        // Slight editorial radius matching the chat bubbles (--radius-lg = 4px)
        // so the selected-row background reads consistently with the messages.
        'rounded-[var(--radius-lg)] transition-colors duration-150',
        'group',
      )}
      style={{
        animationDelay: `${index * 50}ms`,
        background: rowBg,
      }}
      onMouseEnter={(e) => {
        if (allowHover) {
          (e.currentTarget as HTMLDivElement).style.background = 'var(--nm-row-hover)';
        }
      }}
      onMouseLeave={(e) => {
        if (allowHover) {
          (e.currentTarget as HTMLDivElement).style.background = 'transparent';
        }
      }}
    >
      <div className="flex items-center gap-2.5">
        {/* Avatar — small (sm) so rows stay short */}
        <div className="relative shrink-0">
          <AvatarWithStreaming label={displayName.slice(0, 2)} streaming={streaming} size="sm" />
          {completed && !isSelected && (
            <div
              className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full allow-circle"
              style={{
                background: 'var(--color-warning)',
                border: '2px solid var(--nm-card)',
              }}
            />
          )}
        </div>

        {/* Right side — single line: name … unread + time. The row is
            display-only; rename / clear / delete live on the agent profile
            page (Owner ruling 2026-08-27). */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1">
            <span
              className={cn('min-w-0 truncate text-sm', isSelected ? 'font-semibold' : 'font-medium')}
              style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-sans)' }}
            >
              {displayName}
            </span>
            {agent.is_public && !isOwner && (
              <span title={t('layout.agentRow.publicBy', { name: agent.created_by })} className="shrink-0">
                <Globe className="w-3 h-3" style={{ color: 'var(--nm-ink50)' }} />
              </span>
            )}

            {/* Trailing meta — pushed to the right edge */}
            <div className="ml-auto pl-2 flex items-center gap-1.5 shrink-0">
              {unread > 0 && (
                <span
                  className="inline-flex items-center justify-center text-[10px] font-semibold"
                  style={{
                    minWidth: 18,
                    height: 16,
                    padding: '0 5px',
                    borderRadius: 8,
                    background: 'transparent',
                    border: '1px solid var(--nm-ink30)',
                    color: 'var(--nm-ink70)',
                    fontFamily: 'var(--font-mono)',
                  }}
                >
                  {unread > 99 ? '99+' : unread}
                </span>
              )}
              <span
                className="text-[10px]"
                style={{
                  color: unread > 0 ? 'var(--color-silicon)' : 'var(--nm-ink50)',
                  fontWeight: unread > 0 ? 500 : 400,
                  fontFamily: 'var(--font-mono)',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {time}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AvatarWithStreaming — shared with AgentList's collapsed avatar rail so
// both renderings keep the identical streaming-halo treatment.
// ---------------------------------------------------------------------------

export function AvatarWithStreaming({
  label,
  streaming,
  size = 'md',
}: {
  label: string;
  streaming: boolean;
  size?: 'sm' | 'md';
}) {
  if (streaming) {
    return (
      <div className="relative inline-flex items-center justify-center">
        <span
          className="absolute inset-0 rounded-full animate-ping pointer-events-none"
          style={{ border: '2px solid var(--color-warning)' }}
        />
        <RingAvatar species="silicon" label={label} size={size} />
        <Loader2 className="absolute w-3 h-3 animate-spin text-[var(--color-warning)]" />
      </div>
    );
  }
  return <RingAvatar species="silicon" label={label} size={size} />;
}
