/**
 * @file_name: MessengerSection.tsx
 * @author:
 * @date: 2026-08-25
 * @description: Sidebar "Messenger" row — a Settings/System-style nav row
 * that expands in place into a read-only, most-recent-first list merging
 * every agent's last conversation AND every team's group-chat room (avatar +
 * status + name + last-message preview + time). A quick-switch affordance,
 * not a management surface — rename/clear/delete stay on the Agents/Teams
 * dashboard tables ([[DashboardPage.tsx]]).
 */

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useLocation } from 'react-router-dom';
import { MessageSquare, ChevronRight } from 'lucide-react';
import { RingAvatar, AvatarWithStatus, GroupAvatar } from '@/components/nm';
import { useConfigStore, useChatStore, useTeamsStore } from '@/stores';
import { latestMessageMs, teamHasUnread } from '@/lib/unread';
import { cn, formatChatTimestamp } from '@/lib/utils';
import { computeRowMeta, computeTeamRowMeta, sortMessengerItems } from './messengerUtils';
import type { AgentInfo, TeamWithMembers } from '@/types';

const MESSENGER_OPEN_KEY = 'sidebar_messenger_open_v1';

const ROW = 'w-full flex items-center gap-2.5 px-2 py-1.5 rounded-[var(--radius-sm)] text-[13px] font-medium text-left transition-colors text-[var(--nm-ink70)] hover:bg-[var(--nm-row-hover)] hover:text-[var(--nm-ink)]';

export function MessengerSection() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();

  const [open, setOpen] = useState(
    () => typeof window !== 'undefined' && localStorage.getItem(MESSENGER_OPEN_KEY) === '1',
  );
  const toggleOpen = () => {
    setOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(MESSENGER_OPEN_KEY, next ? '1' : '0');
      } catch { /* storage unavailable — open state just won't persist */ }
      return next;
    });
  };

  const activeAgentId = useConfigStore((s) => s.agentId);
  const agents = useConfigStore((s) => s.agents);
  const setAgentId = useConfigStore((s) => s.setAgentId);
  const agentSessions = useChatStore((s) => s.agentSessions);
  const setActiveAgent = useChatStore((s) => s.setActiveAgent);
  const isAgentStreaming = useChatStore((s) => s.isAgentStreaming);

  const teams = useTeamsStore((s) => s.teams);
  const teamsLoaded = useTeamsStore((s) => s.loaded);
  const teamsRefresh = useTeamsStore((s) => s.refresh);

  // Teams aren't fetched by useAutoRefresh the way agents are — the sidebar
  // is the first surface that needs them before the dashboard's Squads tab
  // is ever opened, so it primes the store itself (same guard AgentList used).
  useEffect(() => {
    if (!teamsLoaded) teamsRefresh();
  }, [teamsLoaded, teamsRefresh]);

  // Which team room (if any) is the open view — suppresses the agent active
  // highlight so at most one row reads as "current" at a time.
  const teamChatMatch = location.pathname.match(/^\/app\/teams\/([^/]+)\/chat$/);
  const activeTeamId = teamChatMatch ? teamChatMatch[1] : null;

  // Cheap per-render projection of ONLY what can change sort order — see
  // the identical pattern (and its rationale) this was lifted from in
  // git history's AgentList.tsx. Keeps the resort off the streaming hot path.
  const activitySignature = agents
    .map((a) => {
      const msgs = agentSessions[a.agent_id]?.messages;
      const last = msgs && msgs.length ? msgs[msgs.length - 1] : undefined;
      return `${a.agent_id}:${msgs?.length ?? 0}:${last?.timestamp ?? 0}`;
    })
    .join('|');
  const teamsActivitySignature = teams
    .map((t) => `${t.team.team_id}:${t.last_message_at ?? ''}`)
    .join('|');

  const agentById = useMemo(() => new Map(agents.map((a) => [a.agent_id, a])), [agents]);
  const teamById = useMemo(() => new Map(teams.map((t) => [t.team.team_id, t])), [teams]);

  const items = useMemo(
    () =>
      sortMessengerItems(
        agents,
        teams.map((t) => ({
          team_id: t.team.team_id,
          last_message_at: t.last_message_at,
          created_at: t.team.created_at,
        })),
        (aid) => latestMessageMs(agentSessions[aid]?.messages ?? []),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agents, teams, activitySignature, teamsActivitySignature],
  );

  const handleSelect = (agentId: string) => {
    if (agentId !== activeAgentId) {
      setAgentId(agentId);
      setActiveAgent(agentId);
    }
    if (location.pathname !== '/app/chat' && location.pathname !== '/app') {
      navigate('/app/chat');
    }
  };

  const handleSelectTeam = (teamId: string) => {
    const path = `/app/teams/${teamId}/chat`;
    if (location.pathname !== path) navigate(path);
  };

  return (
    <div className="flex flex-col flex-1 min-h-0 px-2 pt-2">
      <button
        type="button"
        onClick={toggleOpen}
        aria-expanded={open}
        title={t('sidebar.messengerTitle')}
        className={cn(ROW, 'shrink-0')}
      >
        <MessageSquare className="w-4 h-4 shrink-0" />
        <span className="flex-1 min-w-0">{t('sidebar.messenger')}</span>
        <ChevronRight
          className={cn('w-3.5 h-3.5 shrink-0 text-[var(--nm-ink30)] transition-transform duration-150', open && 'rotate-90')}
          aria-hidden
        />
      </button>

      {/* Fills the rest of Zone 2b down to the footer — flex-1 so the list
          grows into all of it instead of stopping short and leaving a
          visible gap above the footer. */}
      {open && (
        <div className="flex-1 min-h-0 overflow-y-auto mt-0.5 pb-2">
          {items.length === 0 ? (
            <div className="px-2.5 py-1.5 text-[11px] text-[var(--nm-ink50)]">
              {t('sidebar.messengerEmpty')}
            </div>
          ) : (
            items.map((item) => {
              if (item.kind === 'agent') {
                const agent = agentById.get(item.id);
                if (!agent) return null;
                return (
                  <MessengerRow
                    key={`agent:${agent.agent_id}`}
                    agent={agent}
                    active={!activeTeamId && agent.agent_id === activeAgentId}
                    streaming={isAgentStreaming(agent.agent_id) || !!agent.active_run}
                    meta={computeRowMeta(agent, agentSessions[agent.agent_id]?.messages ?? [])}
                    onSelect={handleSelect}
                  />
                );
              }
              const team = teamById.get(item.id);
              if (!team) return null;
              return (
                <TeamMessengerRow
                  key={`team:${team.team.team_id}`}
                  team={team}
                  active={activeTeamId === team.team.team_id}
                  meta={computeTeamRowMeta(team)}
                  onSelect={handleSelectTeam}
                />
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

function MessengerRow({
  agent,
  active,
  streaming,
  meta,
  onSelect,
}: {
  agent: AgentInfo;
  active: boolean;
  streaming: boolean;
  meta: { preview: string; timeMs: number };
  onSelect: (agentId: string) => void;
}) {
  const displayName = agent.name || agent.agent_id;
  const time = meta.timeMs ? formatChatTimestamp(meta.timeMs) : '';

  return (
    <button
      type="button"
      onClick={() => onSelect(agent.agent_id)}
      className={cn(
        'w-full flex items-start gap-2.5 px-2 py-2.5 rounded-[var(--radius-sm)] text-left transition-colors',
        active ? 'bg-[var(--nm-row-active)]' : 'hover:bg-[var(--nm-row-hover)]',
      )}
    >
      <AvatarWithStatus status={streaming ? 'warning' : 'success'} className="shrink-0">
        <RingAvatar species="silicon" label={displayName} size="sm" />
      </AvatarWithStatus>
      <span className="flex-1 min-w-0 flex flex-col gap-0.5">
        <span className="flex items-baseline gap-1.5">
          <span className="flex-1 min-w-0 truncate text-[13px] font-semibold text-[var(--nm-ink)]">
            {displayName}
          </span>
          {time && (
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--nm-ink30)]">
              {time}
            </span>
          )}
        </span>
        {meta.preview && (
          <span className="block truncate text-[12px] text-[var(--nm-ink50)]">{meta.preview}</span>
        )}
      </span>
    </button>
  );
}

function TeamMessengerRow({
  team,
  active,
  meta,
  onSelect,
}: {
  team: TeamWithMembers;
  active: boolean;
  meta: { preview: string; timeMs: number };
  onSelect: (teamId: string) => void;
}) {
  const displayName = team.team.name || team.team.team_id;
  const time = meta.timeMs ? formatChatTimestamp(meta.timeMs) : '';
  const initials = displayName
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
  const hasUnread = teamHasUnread(team.last_message_at, team.team.team_id);

  return (
    <button
      type="button"
      onClick={() => onSelect(team.team.team_id)}
      className={cn(
        'w-full flex items-start gap-2.5 px-2 py-2.5 rounded-[var(--radius-sm)] text-left transition-colors',
        active ? 'bg-[var(--nm-row-active)]' : 'hover:bg-[var(--nm-row-hover)]',
      )}
    >
      <AvatarWithStatus status={hasUnread ? 'info' : 'neutral'} className="shrink-0">
        <GroupAvatar
          size="sm"
          members={[{ species: 'carbon' }, { species: 'silicon' }]}
          label={initials}
        />
      </AvatarWithStatus>
      <span className="flex-1 min-w-0 flex flex-col gap-0.5">
        <span className="flex items-baseline gap-1.5">
          <span className="flex-1 min-w-0 truncate text-[13px] font-semibold text-[var(--nm-ink)]">
            {displayName}
          </span>
          {time && (
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--nm-ink30)]">
              {time}
            </span>
          )}
        </span>
        {meta.preview && (
          <span className="block truncate text-[12px] text-[var(--nm-ink50)]">{meta.preview}</span>
        )}
      </span>
    </button>
  );
}
