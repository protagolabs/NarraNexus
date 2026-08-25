/**
 * @file_name: DashboardPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-13
 * @description: Agent status directory with Profile navigation and Team management.
 *
 * Agents view: a directory-style header, creation CTA, search, and a combined
 * table whose rows carry live status and open the dedicated Agent profile.
 * Teams view: the team roster with a door into the existing management modal.
 *
 * Polling FSM driven by dashboardStore (visibility × tauri focus ×
 * any_running) — the loop stays store-owned; this page only ticks it.
 * Paired with setTrayBadge for Tauri; web mode no-op. Handles 429 with
 * exponential backoff (store.onRateLimited).
 */
import { useEffect, useMemo, useState, type ComponentType } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Bot,
  Plus,
  Search,
  MessageSquare,
  Globe,
} from 'lucide-react';
import { useDashboardStore } from '@/stores/dashboardStore';
import { useConfigStore, useChatStore, useTeamsStore } from '@/stores';
import { useCreateAgent } from '@/hooks';
import { api } from '@/lib/api';
import { setTrayBadge, listenTauri } from '@/lib/tauri';
import { Button, ScrollArea, useConfirm } from '@/components/ui';
import { BracketEmptyState, GroupAvatar, RingAvatar } from '@/components/nm';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { ClearTeamDataDialog } from '@/components/teams/ClearTeamDataDialog';
import { TeamRowMenu } from '@/components/layout/TeamRowMenu';
import { AgentTeamAvatars } from '@/components/agents/AgentTeamAvatars';
import { TeamMemberAvatars } from '@/components/agents/TeamMemberAvatars';
import {
  DiscordBrandIcon,
  HomeAssistantBrandIcon,
  LarkBrandIcon,
  NarraMessengerBrandIcon,
  NexusPowerBrandIcon,
  SlackBrandIcon,
  TelegramBrandIcon,
  WeChatBrandIcon,
} from '@/components/icons/ChannelBrandIcons';
import { ClaudeBrandIcon, OpenAIBrandIcon } from '@/components/icons/ModelBrandIcons';
import { getModelBrandIcon } from '@/lib/modelBrandIcons';
import { cn, formatMessageAge } from '@/lib/utils';
import type { AgentInfo, AgentStatus } from '@/types';

type StatusCell = {
  label: string;
  color: string;
};

type BrandIconComponent = ComponentType<{ className?: string }>;

const FRAMEWORK_BRAND_ICONS: Record<string, BrandIconComponent> = {
  claude_code: ClaudeBrandIcon,
  codex_cli: OpenAIBrandIcon,
  nexus_power: NexusPowerBrandIcon,
};

const CHANNEL_BRANDS: Record<string, { label: string; Icon: BrandIconComponent }> = {
  lark: { label: 'Lark / Feishu', Icon: LarkBrandIcon },
  slack: { label: 'Slack', Icon: SlackBrandIcon },
  telegram: { label: 'Telegram', Icon: TelegramBrandIcon },
  wechat: { label: 'WeChat', Icon: WeChatBrandIcon },
  narramessenger: { label: 'NarraMessenger', Icon: NarraMessengerBrandIcon },
  discord: { label: 'Discord', Icon: DiscordBrandIcon },
  home_assistant: { label: 'Home Assistant', Icon: HomeAssistantBrandIcon },
};

export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const statusAgents = useDashboardStore((s) => s.agents);
  const error = useDashboardStore((s) => s.error);
  const setVisibility = useDashboardStore((s) => s.setVisibility);
  const setTauriFocused = useDashboardStore((s) => s.setTauriFocused);
  const onFetchSuccess = useDashboardStore((s) => s.onFetchSuccess);
  const onFetchError = useDashboardStore((s) => s.onFetchError);
  const onRateLimited = useDashboardStore((s) => s.onRateLimited);

  const { agents: rosterAgents, refreshAgents, userId, displayName, setAgentId } = useConfigStore();
  const { teams, refresh: refreshTeams, updateTeam, deleteTeam } = useTeamsStore();
  const { confirm, alert, dialog } = useConfirm();
  const navigate = useNavigate();
  const { setActiveAgent, requestHistoryRefresh, requestWorkspaceRefresh } = useChatStore();
  const { createAgent, creating: creatingAgent } = useCreateAgent();

  // The sidebar's Agents/Squads rows both land here with ?view=; the page's
  // own segmented toggle below writes the same param back so a reload or a
  // shared link keeps whichever tab was open (Owner IA change 2026-08-24 —
  // replaces the old always-listed sidebar chat roster).
  const [searchParams] = useSearchParams();
  const view = searchParams.get('view') === 'teams' ? 'teams' : 'agents';
  const [filterText, setFilterText] = useState('');
  const [renamingTeamId, setRenamingTeamId] = useState<string | null>(null);
  const [teamNameDraft, setTeamNameDraft] = useState('');
  const [clearTeamTarget, setClearTeamTarget] = useState<{ team_id: string; name: string } | null>(null);
  const [clearTeamBusy, setClearTeamBusy] = useState(false);

  useEffect(() => { refreshTeams(); refreshAgents(); }, [refreshTeams, refreshAgents]);

  // --- Visibility API ---
  useEffect(() => {
    const onVis = () => setVisibility(!document.hidden);
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, [setVisibility]);

  // --- Tauri focus/blur ---
  useEffect(() => {
    let unlistenBlur: (() => void) | null = null;
    let unlistenFocus: (() => void) | null = null;
    (async () => {
      unlistenBlur = await listenTauri('tauri://blur', () => setTauriFocused(false));
      unlistenFocus = await listenTauri('tauri://focus', () => setTauriFocused(true));
    })();
    return () => {
      unlistenBlur?.();
      unlistenFocus?.();
    };
  }, [setTauriFocused]);

  // --- Polling loop (cadence owned by dashboardStore.computeInterval) ---
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const res = await api.getDashboardStatus();
        if (!active) return;
        if (!res.success) throw new Error(res.error ?? 'Unknown error');
        onFetchSuccess(res.agents);
        const state = useDashboardStore.getState();
        const running = state.computeRunningCount();
        if (running !== state.lastTrayCount) {
          await setTrayBadge(running);
          useDashboardStore.setState({ lastTrayCount: running });
        }
      } catch (e: unknown) {
        const status = (e as { status?: number })?.status;
        if (status === 429) {
          onRateLimited();
        } else {
          const msg = e instanceof Error ? e.message : String(e);
          onFetchError(msg);
        }
      } finally {
        const interval = useDashboardStore.getState().computeInterval();
        if (active && interval !== Infinity) {
          timer = setTimeout(tick, interval);
        }
      }
    }

    tick();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [onFetchSuccess, onFetchError, onRateLimited]);

  // --- Roster ⨝ status join -------------------------------------------------
  const statusById = useMemo(() => {
    const m = new Map<string, AgentStatus>();
    statusAgents.forEach((a) => m.set(a.agent_id, a));
    return m;
  }, [statusAgents]);

  // Lookup for TeamMemberAvatars — a team's member_agent_ids are only ids;
  // this resolves them against the viewer's own roster the same way the
  // Leader column already does (agents owned by someone else simply aren't
  // in rosterAgents and fall back to id-only rendering).
  const agentsById = useMemo(() => {
    const m = new Map<string, AgentInfo>();
    rosterAgents.forEach((a) => m.set(a.agent_id, a));
    return m;
  }, [rosterAgents]);

  // Public agents (visible in status feed but not in the user's roster) are
  // shown as read-only rows so the old PublicCard capability isn't lost.
  const publicAgents = useMemo(
    () =>
      statusAgents.filter(
        (a) => !a.owned_by_viewer && !rosterAgents.some((r) => r.agent_id === a.agent_id),
      ),
    [statusAgents, rosterAgents],
  );

  // --- Search ---------------------------------------------------------------
  const filteredAgents = useMemo(() => {
    let list = [...rosterAgents];
    if (filterText) {
      const q = filterText.toLowerCase();
      list = list.filter((a) =>
        (a.name || '').toLowerCase().includes(q) || a.agent_id.toLowerCase().includes(q)
      );
    }
    return list;
  }, [rosterAgents, filterText]);

  // --- Per-row actions (ported from the retired sidebar AgentList / TeamChatRow
  // rows — same handlers, now driving a table row's action column instead of a
  // chat-list row) -----------------------------------------------------------

  const handleOpenAgentChat = (agentId: string) => {
    setAgentId(agentId);
    setActiveAgent(agentId);
    navigate('/app/chat');
  };

  const handleOpenTeamChat = (teamId: string) => navigate(`/app/teams/${teamId}/chat`);

  const handleAddAgentToTeam = async (teamId: string) => {
    const id = await createAgent({ teamId });
    if (id) navigate(`/app/teams/${teamId}/chat`);
  };

  const handleStartRenameTeam = (teamId: string, currentName: string) => {
    setTeamNameDraft(currentName);
    setRenamingTeamId(teamId);
  };

  const commitRenameTeam = (teamId: string) => {
    const next = teamNameDraft.trim();
    setRenamingTeamId(null);
    if (next) void updateTeam(teamId, { name: next });
  };

  const doClearTeamData = async (scopes: { chat: boolean; files: boolean; bulletin: boolean }) => {
    if (!clearTeamTarget) return;
    setClearTeamBusy(true);
    try {
      const res = await api.clearTeamData(clearTeamTarget.team_id, scopes);
      if (res.success) {
        if (scopes.chat) requestHistoryRefresh();
        if (scopes.files || scopes.bulletin) requestWorkspaceRefresh();
      } else {
        await alert({ title: t('layout.agentList.deleteFailedTitle'), message: res.error || 'Failed to clear team data', danger: true });
      }
    } catch (err) {
      await alert({ title: t('layout.agentList.deleteFailedTitle'), message: String(err), danger: true });
    } finally {
      setClearTeamBusy(false);
      setClearTeamTarget(null);
    }
  };

  const handleDeleteTeamRow = async (teamId: string) => {
    const team = teams.find((x) => x.team.team_id === teamId);
    const ok = await confirm({
      title: t('layout.agentList.deleteTeamTitle'),
      message: t('layout.agentList.deleteTeamMessage', { name: team?.team.name ?? teamId }),
      confirmText: t('layout.agentList.deleteAction'),
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteTeam(teamId);
    } catch (err) {
      await alert({ title: t('layout.agentList.deleteFailedTitle'), message: err instanceof Error ? err.message : String(err), danger: true });
    }
  };

  const teamLookupForAgent = (aid: string) =>
    teams.filter((tm) => tm.member_agent_ids.includes(aid));

  const statusCellOf = (status: AgentStatus | undefined): StatusCell => {
    if (!status) return { label: '—', color: 'var(--nm-ink30)' };
    if (status.owned_by_viewer) {
      const health = status.health;
      if (health === 'error') {
        return { label: t('dashboard.summary.chip.error'), color: 'var(--color-error)' };
      }
      if (health === 'warning') {
        return { label: t('dashboard.summary.chip.blocked'), color: 'var(--color-warning)' };
      }
      if (health === 'paused') {
        return { label: t('dashboard.summary.chip.paused'), color: 'var(--color-warning)' };
      }
    }
    if (status.status.kind !== 'idle') {
      return { label: t('dashboard.summary.chip.running'), color: 'var(--color-success)' };
    }
    return { label: t('dashboard.summary.chip.idle'), color: 'var(--nm-ink30)' };
  };

  const inputBox =
    'flex items-center gap-2 h-10 px-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)]';
  const agentChatButtonClass =
    'inline-flex items-center gap-1.5 rounded-[var(--radius-xs)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--nm-ink70)] shadow-[var(--nm-elev-1)] transition-colors hover:border-[var(--nm-ink30)] hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]';

  return (
    <ScrollArea className="h-full" viewportClassName="px-6 py-6">
      <div className="max-w-[1180px] mx-auto space-y-5">
        {dialog}
        {clearTeamTarget && (
          <ClearTeamDataDialog
            teamName={clearTeamTarget.name}
            busy={clearTeamBusy}
            onCancel={() => setClearTeamTarget(null)}
            onConfirm={doClearTeamData}
          />
        )}

        {/* Directory header — title/count and creation stay visible above the
            searchable view tabs, following the Agents dashboard hierarchy. */}
        <header className="flex items-start justify-between gap-6 pr-10 pb-5 border-b border-[var(--nm-hairline)]">
          <div className="flex items-center gap-3 min-w-0">
            <Bot className="w-6 h-6 shrink-0 text-[var(--nm-ink70)]" aria-hidden="true" />
            <div className="flex items-baseline gap-2.5">
              <h1
                className="text-2xl font-bold tracking-tight"
                style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
              >
                {view === 'agents' ? t('sidebar.agents') : t('sidebar.teams')}
              </h1>
              <span className="text-[15px] font-medium text-[var(--nm-ink50)]">
                {view === 'agents' ? rosterAgents.length : teams.length}
              </span>
            </div>
          </div>
          <Button
            type="button"
            size="sm"
            className="shrink-0 gap-1.5"
            onClick={() => navigate(view === 'agents' ? '/app/agents/new' : '/app/teams/new')}
          >
            <Plus className="w-4 h-4" />
            {t(view === 'agents' ? 'pages.dashboard.newAgent' : 'pages.dashboard.newTeam')}
          </Button>
        </header>

        <div className="flex items-center gap-4">
          {view === 'agents' && (
            <div className={cn(inputBox, 'w-full max-w-[450px]')}>
              <Search className="w-4 h-4 text-[var(--nm-ink30)]" />
              <input
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                placeholder={t('pages.manageAgents.searchPlaceholder')}
                className="flex-1 bg-transparent text-[13px] focus:outline-none text-[var(--nm-ink)] placeholder:text-[var(--nm-ink30)]"
              />
            </div>
          )}
        </div>

        {error && (
          <div
            className="p-3 text-sm rounded-[var(--radius-sm)]"
            style={{
              background: 'var(--color-error)',
              color: 'white',
              border: '1px solid var(--color-error)',
            }}
          >
            {error}
          </div>
        )}

        {view === 'agents' && (
          <>
            {/* Agent status directory; configuration lives in Profile Settings. */}
            <div data-testid="agent-directory-table" className="font-sans overflow-hidden">
              <div className="grid grid-cols-[minmax(210px,280px)_90px_96px_110px_120px_minmax(140px,1fr)_110px_80px] items-center gap-0 px-4 py-2.5 text-[10px] font-medium uppercase tracking-[0.1em] text-[var(--nm-ink50)] bg-[var(--nm-paper)]">
                <span>{t('pages.manageAgents.colNameId')}</span>
                <span>{t('pages.dashboard.colStatus')}</span>
                <span>{t('pages.manageAgents.colTeams')}</span>
                <span>{t('pages.dashboard.colChannels')}</span>
                <span>{t('pages.dashboard.colFramework')}</span>
                <span>{t('pages.dashboard.colModel')}</span>
                <span>{t('pages.dashboard.colLastActive')}</span>
                <span></span>
              </div>
              {filteredAgents.length === 0 && publicAgents.length === 0 ? (
                <div className="px-4 py-8">
                  <BracketEmptyState
                    label={t('pages.dashboard.emptyLabel')}
                    hint={t('pages.dashboard.emptyHint')}
                  />
                </div>
              ) : (
                <>
                  {filteredAgents.map((a) => {
                    const status = statusById.get(a.agent_id);
                    const lastActiveAt = status?.status.last_activity_at;
                    const aTeams = teamLookupForAgent(a.agent_id);
                    const cell = statusCellOf(status);
                    const isOwnerRow = a.created_by === userId;
                    const FrameworkIcon = a.agent_framework
                      ? FRAMEWORK_BRAND_ICONS[a.agent_framework] ?? Bot
                      : null;
                    const ModelIcon = a.model ? getModelBrandIcon(a.model) ?? Bot : null;
                    const boundChannels = a.bound_channels ?? [];
                    return (
                      <div
                        key={a.agent_id}
                        data-testid={`dash-row-${a.agent_id}`}
                        role="link"
                        tabIndex={0}
                        aria-label={t('pages.dashboard.openProfile', { name: a.name || a.agent_id })}
                        onClick={() =>
                          navigate(`/app/agents/${encodeURIComponent(a.agent_id)}`, {
                            state: { from: 'dashboard' },
                          })
                        }
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            navigate(`/app/agents/${encodeURIComponent(a.agent_id)}`, {
                              state: { from: 'dashboard' },
                            });
                          }
                        }}
                      >
                        <div
                          className="group grid grid-cols-[minmax(210px,280px)_90px_96px_110px_120px_minmax(140px,1fr)_110px_80px] items-center gap-0 px-4 py-3 cursor-pointer bg-[var(--nm-card)] transition-colors hover:bg-[var(--nm-row-hover)]"
                        >
                          <span className="flex items-center gap-2.5 min-w-0">
                            <RingAvatar
                              species="silicon"
                              label={(a.name || a.agent_id).slice(0, 2)}
                              size="sm"
                              className="shrink-0"
                            />
                            <span className="min-w-0 flex flex-col gap-px">
                              <span className="flex items-center gap-1.5">
                                <span className="text-[13px] font-semibold text-[var(--nm-ink)] truncate">{a.name || a.agent_id}</span>
                                {a.is_public && !isOwnerRow && (
                                  <span title={t('layout.agentRow.publicBy', { name: a.created_by })} className="shrink-0">
                                    <Globe className="w-3 h-3" style={{ color: 'var(--nm-ink50)' }} />
                                  </span>
                                )}
                              </span>
                              <span className="text-[10px] text-[var(--nm-ink30)] truncate">{a.agent_id}</span>
                            </span>
                          </span>
                          <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--nm-ink70)]">
                            <span className="w-[7px] h-[7px] rounded-full" style={{ background: cell.color }} />
                            {cell.label}
                          </span>
                          <AgentTeamAvatars agentId={a.agent_id} teams={aTeams} />
                          <span
                            data-testid={`channels-${a.agent_id}`}
                            className="flex min-w-0 items-center gap-1.5 pr-2"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {boundChannels.length === 0 ? (
                              <span className="text-[11px] text-[var(--nm-ink30)]">—</span>
                            ) : (
                              <TooltipProvider delayDuration={180} skipDelayDuration={80}>
                                {boundChannels.map((channel) => {
                                  const brand = CHANNEL_BRANDS[channel];
                                  const ChannelIcon = brand?.Icon ?? Bot;
                                  const label = brand?.label ?? channel;
                                  return (
                                    <Tooltip key={channel}>
                                      <TooltipTrigger asChild>
                                        <span
                                          data-channel={channel}
                                          aria-label={label}
                                          tabIndex={0}
                                          className="inline-flex h-5 w-5 items-center justify-center rounded-[var(--radius-xs)] outline-none transition-transform hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-[var(--nm-ink30)]"
                                        >
                                          <span aria-hidden="true">
                                            <ChannelIcon className="h-4 w-4" />
                                          </span>
                                        </span>
                                      </TooltipTrigger>
                                      <TooltipContent side="top">{label}</TooltipContent>
                                    </Tooltip>
                                  );
                                })}
                              </TooltipProvider>
                            )}
                          </span>
                          <span
                            data-testid={`framework-${a.agent_id}`}
                            className="inline-flex min-w-0 items-center gap-1.5 text-[12px] font-medium text-[var(--nm-ink70)]"
                          >
                            {FrameworkIcon && (
                              <span aria-hidden="true" className="shrink-0">
                                <FrameworkIcon
                                  className={cn(
                                    'h-3.5 w-3.5',
                                    FrameworkIcon === OpenAIBrandIcon && 'dark:invert',
                                  )}
                                />
                              </span>
                            )}
                            <span className="truncate">
                              {formatFramework(a.agent_framework)}
                            </span>
                          </span>
                          <span
                            data-testid={`model-${a.agent_id}`}
                            className="inline-flex min-w-0 items-center gap-1.5 text-[11px] text-[var(--nm-ink50)]"
                            title={a.model || undefined}
                          >
                            {ModelIcon && (
                              <span aria-hidden="true" className="shrink-0">
                                <ModelIcon
                                  className={cn(
                                    'h-3.5 w-3.5',
                                    ModelIcon === OpenAIBrandIcon && 'dark:invert',
                                  )}
                                />
                              </span>
                            )}
                            <span className="truncate">{a.model || '—'}</span>
                          </span>
                          <span
                            className="text-[11px] text-[var(--nm-ink50)] truncate"
                            title={lastActiveAt || undefined}
                          >
                            {lastActiveAt ? formatMessageAge(lastActiveAt, i18n.language) : '—'}
                          </span>
                          <span
                            className="flex items-center justify-end gap-1"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <button
                              type="button"
                              onClick={() => handleOpenAgentChat(a.agent_id)}
                              title={t('pages.manageAgents.openChat')}
                              aria-label={t('pages.manageAgents.openChat')}
                              className={agentChatButtonClass}
                            >
                              <MessageSquare className="w-3.5 h-3.5" />
                              {t('pages.dashboard.chat')}
                            </button>
                          </span>
                        </div>
                      </div>
                    );
                  })}
                  {/* Public agents — read-only rows (no roster entry, no
                      management), preserving the old PublicCard visibility. */}
                  {publicAgents.map((a) => {
                    const cell = statusCellOf(a);
                    return (
                      <div
                        key={a.agent_id}
                        className="grid grid-cols-[minmax(210px,280px)_90px_96px_110px_120px_minmax(140px,1fr)_110px_80px] items-center gap-0 px-4 py-3 bg-[var(--nm-card)]"
                      >
                        <span className="flex items-center gap-2.5 min-w-0">
                          <RingAvatar species="silicon" label={(a.name || a.agent_id).slice(0, 2)} size="sm" className="shrink-0" />
                          <span className="min-w-0 flex flex-col gap-px">
                            <span className="text-[13px] font-semibold text-[var(--nm-ink)] truncate">{a.name}</span>
                            {a.description && (
                              <span className="text-[11px] text-[var(--nm-ink50)] italic truncate">{a.description}</span>
                            )}
                          </span>
                        </span>
                        <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--nm-ink70)]">
                          <span className="w-[7px] h-[7px] rounded-full" style={{ background: cell.color }} />
                          {cell.label}
                        </span>
                        <span className="text-[11px] text-[var(--nm-ink30)] italic">—</span>
                        <span className="text-[11px] text-[var(--nm-ink30)] italic">—</span>
                        <span className="text-[11px] text-[var(--nm-ink30)] italic">—</span>
                        <span className="text-[11px] text-[var(--nm-ink30)] italic">—</span>
                        <span
                          className="text-[11px] text-[var(--nm-ink50)] truncate"
                          title={a.status.last_activity_at || undefined}
                        >
                          {a.status.last_activity_at
                            ? formatMessageAge(a.status.last_activity_at, i18n.language)
                            : '—'}
                        </span>
                        <span className="flex items-center justify-end">
                          <button
                            type="button"
                            onClick={() => handleOpenAgentChat(a.agent_id)}
                            title={t('pages.manageAgents.openChat')}
                            aria-label={t('pages.manageAgents.openChat')}
                            className={agentChatButtonClass}
                          >
                            <MessageSquare className="w-3.5 h-3.5" />
                            {t('pages.dashboard.chat')}
                          </button>
                        </span>
                      </div>
                    );
                  })}
                </>
              )}
            </div>

          </>
        )}

        {view === 'teams' && (
          <>
            <div className="rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] overflow-hidden">
              <div className="grid grid-cols-[1fr_140px_132px_140px_100px_72px] items-center gap-0 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--nm-ink50)] border-b border-[var(--nm-hairline)] bg-[var(--nm-paper)]">
                <span>{t('pages.dashboard.colTeam')}</span>
                <span>{t('pages.dashboard.colLeader')}</span>
                <span>{t('pages.dashboard.colMembers')}</span>
                <span>{t('pages.dashboard.colCreatedBy')}</span>
                <span>{t('pages.manageAgents.colSource')}</span>
                <span></span>
              </div>
              {teams.length === 0 ? (
                <div className="px-4 py-8">
                  <BracketEmptyState
                    label={t('pages.dashboard.noTeamsLabel')}
                    hint={t('pages.dashboard.noTeamsHint')}
                  />
                </div>
              ) : (
                teams.map((tm) => {
                  const leader = tm.team.lead_agent_id
                    ? rosterAgents.find((a) => a.agent_id === tm.team.lead_agent_id)
                    : undefined;
                  const isRenamingRow = renamingTeamId === tm.team.team_id;
                  return (
                    <div
                      key={tm.team.team_id}
                      className="grid grid-cols-[1fr_140px_132px_140px_100px_72px] items-center gap-0 px-3 py-2.5 border-b border-[var(--nm-hairline)] bg-[var(--nm-card)]"
                    >
                      <span className="flex items-center gap-2.5 min-w-0">
                        <span data-testid={`team-avatar-${tm.team.team_id}`} className="shrink-0">
                          <GroupAvatar
                            size="sm"
                            members={[{ species: 'carbon' }, { species: 'silicon' }]}
                            label={teamAvatarInitials(tm.team.name)}
                            title={tm.team.name}
                          />
                        </span>
                        {isRenamingRow ? (
                          <input
                            autoFocus
                            value={teamNameDraft}
                            onChange={(e) => setTeamNameDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') commitRenameTeam(tm.team.team_id);
                              if (e.key === 'Escape') setRenamingTeamId(null);
                            }}
                            onBlur={() => commitRenameTeam(tm.team.team_id)}
                            className="flex-1 min-w-0 px-2 py-0.5 text-[13px] text-[var(--nm-ink)] bg-[var(--nm-paper-warm)] border border-[var(--nm-ink)] rounded-[var(--radius-xs)] focus:outline-none"
                          />
                        ) : (
                          <span className="text-[13px] font-semibold text-[var(--nm-ink)] truncate">{tm.team.name}</span>
                        )}
                      </span>
                      <span className="flex items-center gap-1.5 min-w-0 text-[12px] text-[var(--nm-ink70)]">
                        {leader ? (
                          <>
                            <RingAvatar species="silicon" label={(leader.name || leader.agent_id).slice(0, 2)} size="sm" className="shrink-0" />
                            <span className="truncate">{leader.name || leader.agent_id}</span>
                          </>
                        ) : (
                          <span className="text-[11px] text-[var(--nm-ink30)] italic">{t('pages.dashboard.noLeader')}</span>
                        )}
                      </span>
                      <TeamMemberAvatars
                        memberAgentIds={tm.member_agent_ids}
                        agentsById={agentsById}
                        statusById={statusById}
                        currentUserId={userId}
                        currentUserDisplayName={displayName}
                        max={2}
                      />
                      <span data-testid="team-created-by" className="flex items-center gap-1.5 min-w-0 text-[12px] text-[var(--nm-ink70)]">
                        <RingAvatar species="carbon" label={(displayName || userId).slice(0, 2)} size="sm" className="shrink-0" />
                        <span className="truncate">{displayName || userId}</span>
                      </span>
                      <span className="text-[11px] text-[var(--nm-ink50)]">
                        {tm.team.source === 'bundle'
                          ? t('pages.manageAgents.fromBundle')
                          : t('pages.manageAgents.createdLocally')}
                      </span>
                      <span className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => handleOpenTeamChat(tm.team.team_id)}
                          title={t('pages.manageAgents.openChat')}
                          aria-label={t('pages.manageAgents.openChat')}
                          className="p-1 rounded-[var(--radius-xs)] text-[var(--nm-ink50)] hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)] transition-colors"
                        >
                          <MessageSquare className="w-3.5 h-3.5" />
                        </button>
                        <TeamRowMenu
                          onAddAgent={() => handleAddAgentToTeam(tm.team.team_id)}
                          addingAgent={creatingAgent}
                          onRename={() => handleStartRenameTeam(tm.team.team_id, tm.team.name)}
                          onClearData={() => setClearTeamTarget({ team_id: tm.team.team_id, name: tm.team.name })}
                          onDelete={() => handleDeleteTeamRow(tm.team.team_id)}
                        />
                      </span>
                    </div>
                  );
                })
              )}
            </div>
            <p className="text-[11px] leading-relaxed text-[var(--nm-ink30)]">
              {t('pages.dashboard.teamsTip')}
            </p>
          </>
        )}
      </div>
    </ScrollArea>
  );
}

function teamAvatarInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length > 1) {
    return words.slice(0, 2).map((word) => word.charAt(0).toUpperCase()).join('');
  }
  return name.slice(0, 2).toUpperCase();
}

function formatFramework(framework?: string): string {
  if (!framework) return '—';
  if (framework === 'claude_code') return 'Claude Code';
  if (framework === 'codex_cli') return 'Codex';
  return framework
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

export default DashboardPage;
