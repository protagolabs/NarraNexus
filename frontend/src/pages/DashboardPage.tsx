/**
 * @file_name: DashboardPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-13
 * @description: Chat UI v4 Dashboard — status monitoring + agent management
 * merged into one page (absorbs the former ManageAgentsPage).
 *
 * Agents view: 4 stat tiles (Running / Queued / Errors / Cost today),
 * search + team filter toolbar, bulk-action bar (select-all, add/remove
 * team, delete), and a combined table whose rows carry live status AND are
 * chevron-expandable into the full per-agent detail (verb line, banners,
 * queue/metrics, sessions, jobs, sparkline, recent feed). Teams view: the
 * team roster with a door into the existing management modal.
 *
 * Polling FSM driven by dashboardStore (visibility × tauri focus ×
 * any_running) — the loop stays store-owned; this page only ticks it.
 * Paired with setTrayBadge for Tauri; web mode no-op. Handles 429 with
 * exponential backoff (store.onRateLimited).
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronRight,
  Loader2,
  Search,
  Trash2,
  UserCheck,
  UserMinus,
  AlertTriangle,
  Users2,
} from 'lucide-react';
import { useDashboardStore } from '@/stores/dashboardStore';
import { useConfigStore, useTeamsStore } from '@/stores';
import { api } from '@/lib/api';
import { setTrayBadge, listenTauri } from '@/lib/tauri';
import { Button, ScrollArea, useConfirm } from '@/components/ui';
import { BracketSectionLabel, BracketEmptyState, KPITile, RingAvatar } from '@/components/nm';
import { AttentionBanners } from '@/components/dashboard/AttentionBanners';
import { SessionSection } from '@/components/dashboard/SessionSection';
import { JobsSection } from '@/components/dashboard/JobsSection';
import { QueueBar } from '@/components/dashboard/QueueBar';
import { Sparkline } from '@/components/dashboard/Sparkline';
import { RecentFeed } from '@/components/dashboard/RecentFeed';
import { MetricsRow } from '@/components/dashboard/MetricsRow';
import { TeamManagementModal } from '@/components/teams/TeamManagementModal';
import { cn } from '@/lib/utils';
import type { AgentStatus, OwnedAgentStatus } from '@/types';

type StatusCell = {
  label: string;
  color: string;
};

export function DashboardPage() {
  const { t } = useTranslation();
  const statusAgents = useDashboardStore((s) => s.agents);
  const error = useDashboardStore((s) => s.error);
  const setVisibility = useDashboardStore((s) => s.setVisibility);
  const setTauriFocused = useDashboardStore((s) => s.setTauriFocused);
  const onFetchSuccess = useDashboardStore((s) => s.onFetchSuccess);
  const onFetchError = useDashboardStore((s) => s.onFetchError);
  const onRateLimited = useDashboardStore((s) => s.onRateLimited);

  const { agents: rosterAgents, refreshAgents } = useConfigStore();
  const { teams, refresh: refreshTeams, addMember, removeMember } = useTeamsStore();
  const { confirm, alert, dialog } = useConfirm();

  const [view, setView] = useState<'agents' | 'teams'>('agents');
  // v4 table: multiple rows can be expanded at once (a Set, not the old
  // single expandedId — the mirror doc called this out explicitly).
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastClickedIdx, setLastClickedIdx] = useState<number | null>(null);
  const [filterTeam, setFilterTeam] = useState<string>(''); // '' / 'untagged' / 'imported' / <team_id>
  const [filterText, setFilterText] = useState('');
  const [busy, setBusy] = useState(false);
  const [bulkTeamPicker, setBulkTeamPicker] = useState<string>('');
  const [teamsMgmtOpen, setTeamsMgmtOpen] = useState(false);

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

  // Public agents (visible in status feed but not in the user's roster) are
  // shown as read-only rows so the old PublicCard capability isn't lost.
  const publicAgents = useMemo(
    () =>
      statusAgents.filter(
        (a) => !a.owned_by_viewer && !rosterAgents.some((r) => r.agent_id === a.agent_id),
      ),
    [statusAgents, rosterAgents],
  );

  // --- Stat tiles (owned agents only — public rows carry no metrics) -------
  const stats = useMemo(() => {
    const owned = statusAgents.filter(
      (a): a is OwnedAgentStatus => a.owned_by_viewer,
    );
    let queued = 0;
    let errors = 0;
    let costCents = 0;
    let running = 0;
    owned.forEach((a) => {
      queued += a.queue?.total ?? 0;
      errors += a.metrics_today?.errors ?? 0;
      costCents += a.metrics_today?.token_cost_cents ?? 0;
      if (a.status.kind !== 'idle') running += 1;
    });
    return { running, queued, errors, cost: `$${(costCents / 100).toFixed(2)}` };
  }, [statusAgents]);

  // --- Filters (inherited from ManageAgentsPage) ---------------------------
  const importedAgentIds = useMemo(() => {
    const s = new Set<string>();
    teams.forEach((tm) => {
      if (tm.team.source === 'bundle') {
        tm.member_agent_ids.forEach((id) => s.add(id));
      }
    });
    return s;
  }, [teams]);

  const filteredAgents = useMemo(() => {
    let list = [...rosterAgents];
    if (filterText) {
      const q = filterText.toLowerCase();
      list = list.filter((a) =>
        (a.name || '').toLowerCase().includes(q) || a.agent_id.toLowerCase().includes(q)
      );
    }
    if (filterTeam === 'untagged') {
      const taggedIds = new Set<string>();
      teams.forEach((tm) => tm.member_agent_ids.forEach((id) => taggedIds.add(id)));
      list = list.filter((a) => !taggedIds.has(a.agent_id));
    } else if (filterTeam === 'imported') {
      list = list.filter((a) => importedAgentIds.has(a.agent_id));
    } else if (filterTeam) {
      const team = teams.find((tm) => tm.team.team_id === filterTeam);
      const memberIds = new Set(team?.member_agent_ids || []);
      list = list.filter((a) => memberIds.has(a.agent_id));
    }
    return list;
  }, [rosterAgents, filterText, filterTeam, teams, importedAgentIds]);

  const allSelected = filteredAgents.length > 0
    && filteredAgents.every((a) => selected.has(a.agent_id));

  const toggleAll = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) filteredAgents.forEach((a) => next.delete(a.agent_id));
      else filteredAgents.forEach((a) => next.add(a.agent_id));
      return next;
    });
  };

  const toggleSelect = (agentId: string, idx: number, ev: React.MouseEvent) => {
    if (ev.shiftKey && lastClickedIdx !== null) {
      const [a, b] = [Math.min(idx, lastClickedIdx), Math.max(idx, lastClickedIdx)];
      const target = !selected.has(agentId);
      setSelected((prev) => {
        const next = new Set(prev);
        for (let i = a; i <= b; i++) {
          const id = filteredAgents[i].agent_id;
          if (target) next.add(id); else next.delete(id);
        }
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(agentId)) next.delete(agentId);
        else next.add(agentId);
        return next;
      });
    }
    setLastClickedIdx(idx);
  };

  const toggleExpand = (agentId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(agentId)) next.delete(agentId);
      else next.add(agentId);
      return next;
    });
  };

  // --- Bulk ops (inherited; per-row loop, partial success surfaced) --------
  const handleBulkDelete = async () => {
    if (selected.size === 0) return;
    const ok = await confirm({
      title: t('pages.manageAgents.deleteConfirmTitle', { count: selected.size }),
      message: t('pages.manageAgents.deleteConfirmMessage'),
      confirmText: t('pages.manageAgents.deleteConfirmText', { count: selected.size }),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    let success = 0;
    const failed: string[] = [];
    for (const aid of Array.from(selected)) {
      try {
        await api.deleteAgent(aid);
        success += 1;
      } catch {
        failed.push(aid);
      }
    }
    await refreshAgents();
    await refreshTeams();
    setSelected(new Set());
    setBusy(false);
    await alert({
      title: t('pages.manageAgents.bulkDeleteCompleteTitle'),
      message: failed.length
        ? t('pages.manageAgents.bulkDeleteResultWithFailures', {
            success,
            failedCount: failed.length,
            failedIds: `${failed.slice(0, 3).join(', ')}${failed.length > 3 ? '…' : ''}`,
          })
        : t('pages.manageAgents.bulkDeleteResult', { success }),
      danger: failed.length > 0,
    });
  };

  const handleBulkAddToTeam = async () => {
    if (selected.size === 0 || !bulkTeamPicker) return;
    setBusy(true);
    for (const aid of Array.from(selected)) {
      try { await addMember(bulkTeamPicker, aid); } catch { /* per-row best effort */ }
    }
    await refreshTeams();
    setBusy(false);
    await alert({
      title: t('pages.manageAgents.addedToTeamTitle'),
      message: t('pages.manageAgents.addedToTeamMessage', { count: selected.size }),
    });
  };

  const handleBulkRemoveFromTeam = async () => {
    if (selected.size === 0 || !bulkTeamPicker) return;
    setBusy(true);
    for (const aid of Array.from(selected)) {
      try { await removeMember(bulkTeamPicker, aid); } catch { /* per-row best effort */ }
    }
    await refreshTeams();
    setBusy(false);
  };

  const teamLookupForAgent = (aid: string) =>
    teams.filter((tm) => tm.member_agent_ids.includes(aid)).map((tm) => tm.team);

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
    'flex items-center gap-2 h-[34px] px-3 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)]';

  return (
    <ScrollArea className="h-full" viewportClassName="px-6 py-6">
      <div className="max-w-[960px] mx-auto space-y-4">
        {dialog}
        <TeamManagementModal open={teamsMgmtOpen} onClose={() => setTeamsMgmtOpen(false)} />

        {/* Header — title + Agents/Teams toggle + meta. pr-10 reserves the
            top-right corner for MainLayout's close (X). */}
        <div className="flex items-center justify-between gap-3 pr-10">
          <div className="flex items-center gap-4 min-w-0">
            <h1
              className="text-xl font-bold tracking-tight"
              style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
            >
              {t('pages.dashboard.title')}
            </h1>
            <div className="inline-flex items-center gap-0.5 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] p-0.5">
              {(['agents', 'teams'] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setView(v)}
                  className={cn(
                    'rounded-[var(--radius-sm)] px-3.5 py-1 text-[12px] transition-colors',
                    view === v
                      ? 'bg-[var(--nm-raised)] font-semibold text-[var(--nm-ink)]'
                      : 'font-medium text-[var(--nm-ink50)] hover:text-[var(--nm-ink)]',
                  )}
                >
                  {v === 'agents' ? t('sidebar.agents') : t('sidebar.teams')}
                </button>
              ))}
            </div>
          </div>
          <BracketSectionLabel>
            {view === 'agents'
              ? t('pages.manageAgents.summary', {
                  shown: filteredAgents.length,
                  selected: selected.size,
                  total: rosterAgents.length,
                })
              : t('pages.dashboard.teamsCount', { count: teams.length })}
          </BracketSectionLabel>
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
            {/* Stat tiles */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
              <KPITile label={t('pages.dashboard.statRunning')} value={stats.running} />
              <KPITile label={t('pages.dashboard.statQueued')} value={stats.queued} />
              <KPITile label={t('pages.dashboard.statErrors')} value={stats.errors} />
              <KPITile label={t('pages.dashboard.statCostToday')} value={stats.cost} upIsGood={false} />
            </div>

            {/* Search + filter toolbar */}
            <div className="flex items-center gap-2">
              <div className={cn(inputBox, 'flex-1')}>
                <Search className="w-3.5 h-3.5 text-[var(--nm-ink30)]" />
                <input
                  value={filterText}
                  onChange={(e) => setFilterText(e.target.value)}
                  placeholder={t('pages.manageAgents.searchPlaceholder')}
                  className="flex-1 bg-transparent text-[13px] focus:outline-none text-[var(--nm-ink)] placeholder:text-[var(--nm-ink30)]"
                />
              </div>
              <select
                value={filterTeam}
                onChange={(e) => setFilterTeam(e.target.value)}
                className={cn(inputBox, 'shrink-0 text-[12px] font-medium text-[var(--nm-ink70)] cursor-pointer')}
              >
                <option value="">{t('pages.manageAgents.filterAll')}</option>
                <option value="untagged">{t('pages.manageAgents.filterUntagged')}</option>
                <option value="imported">{t('pages.manageAgents.filterImported')}</option>
                <optgroup label={t('pages.manageAgents.filterByTeam')}>
                  {teams.map((tm) => (
                    <option key={tm.team.team_id} value={tm.team.team_id}>{tm.team.name}</option>
                  ))}
                </optgroup>
              </select>
            </div>

            {/* Bulk actions bar */}
            <div className={cn(
              'flex items-center gap-2 rounded-[var(--radius-sm)] border p-2 px-3',
              selected.size > 0
                ? 'border-[var(--border-strong)] bg-[var(--nm-paper)]'
                : 'border-[var(--nm-hairline)] bg-[var(--nm-paper)]',
            )}>
              <label className="flex items-center gap-2 text-[12px] text-[var(--nm-ink70)] cursor-pointer select-none" onClick={toggleAll}>
                <TableCheckbox checked={allSelected} />
                {allSelected ? t('pages.manageAgents.unselectAll') : t('pages.manageAgents.selectAllShown')}
              </label>
              <span className="flex-1" />
              <select
                value={bulkTeamPicker}
                onChange={(e) => setBulkTeamPicker(e.target.value)}
                disabled={selected.size === 0}
                className="h-[30px] rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-transparent px-2 text-[12px] font-medium text-[var(--nm-ink70)] disabled:opacity-50"
              >
                <option value="">{t('pages.manageAgents.pickTeam')}</option>
                {teams.map((tm) => (
                  <option key={tm.team.team_id} value={tm.team.team_id}>{tm.team.name}</option>
                ))}
              </select>
              <Button
                onClick={handleBulkAddToTeam}
                disabled={selected.size === 0 || !bulkTeamPicker || busy}
                size="sm"
                variant="outline"
                className="gap-1"
              >
                <UserCheck className="w-3.5 h-3.5" /> {t('pages.manageAgents.addToTeam')}
              </Button>
              <Button
                onClick={handleBulkRemoveFromTeam}
                disabled={selected.size === 0 || !bulkTeamPicker || busy}
                size="sm"
                variant="outline"
                className="gap-1"
              >
                <UserMinus className="w-3.5 h-3.5" /> {t('pages.manageAgents.removeFromTeam')}
              </Button>
              <Button
                onClick={handleBulkDelete}
                disabled={selected.size === 0 || busy}
                size="sm"
                className="gap-1 bg-transparent border border-[color:rgba(201,90,77,0.3)] text-[var(--color-error)] hover:bg-[color:rgba(201,90,77,0.08)] disabled:text-[color:rgba(201,90,77,0.45)]"
              >
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                {t('pages.manageAgents.delete')}
              </Button>
            </div>

            {filterTeam === 'imported' && (
              <div className="text-[11px] text-[var(--nm-ink50)] flex items-center gap-1.5">
                <AlertTriangle className="w-3 h-3 text-[var(--color-warning)]" />
                {t('pages.manageAgents.importedHelper')}
              </div>
            )}

            {/* Combined status + management table */}
            <div className="rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] overflow-hidden">
              <div className="grid grid-cols-[36px_1fr_130px_140px_130px] items-center gap-0 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--nm-ink50)] border-b border-[var(--nm-hairline)] bg-[var(--nm-paper)]">
                <span></span>
                <span>{t('pages.manageAgents.colNameId')}</span>
                <span>{t('pages.dashboard.colStatus')}</span>
                <span>{t('pages.manageAgents.colTeams')}</span>
                <span>{t('pages.manageAgents.colSource')}</span>
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
                  {filteredAgents.map((a, idx) => {
                    const isSel = selected.has(a.agent_id);
                    const isOpen = expanded.has(a.agent_id);
                    const status = statusById.get(a.agent_id);
                    const owned = status?.owned_by_viewer ? (status as OwnedAgentStatus) : null;
                    const aTeams = teamLookupForAgent(a.agent_id);
                    const cell = statusCellOf(status);
                    const isImported = importedAgentIds.has(a.agent_id);
                    return (
                      <div key={a.agent_id} data-testid={`dash-row-${a.agent_id}`}>
                        <div
                          className={cn(
                            'grid grid-cols-[36px_1fr_130px_140px_130px] items-center gap-0 px-3 py-2.5 border-b border-[var(--nm-hairline)] cursor-pointer transition-colors',
                            isOpen ? 'bg-[var(--nm-paper)]' : 'bg-[var(--nm-card)] hover:bg-[var(--nm-paper)]',
                          )}
                          onClick={() => toggleExpand(a.agent_id)}
                        >
                          <span
                            onClick={(e) => { e.stopPropagation(); toggleSelect(a.agent_id, idx, e); }}
                            className="cursor-pointer"
                          >
                            <TableCheckbox checked={isSel} />
                          </span>
                          <span className="flex items-center gap-2.5 min-w-0">
                            <ChevronRight
                              className={cn(
                                'w-3 h-3 shrink-0 text-[var(--nm-ink30)] transition-transform duration-150',
                                isOpen && 'rotate-90',
                              )}
                            />
                            <RingAvatar
                              species="silicon"
                              label={(a.name || a.agent_id).slice(0, 2)}
                              size="sm"
                              className="shrink-0"
                            />
                            <span className="min-w-0 flex flex-col gap-px">
                              <span className="text-[13px] font-semibold text-[var(--nm-ink)] truncate">{a.name || a.agent_id}</span>
                              <span className="font-mono text-[10px] text-[var(--nm-ink30)] truncate">{a.agent_id}</span>
                            </span>
                          </span>
                          <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-[var(--nm-ink70)]">
                            <span className="w-[7px] h-[7px] rounded-full" style={{ background: cell.color }} />
                            {cell.label}
                          </span>
                          <span className="flex flex-wrap gap-1 pr-2">
                            {aTeams.length === 0 && (
                              <span className="text-[11px] text-[var(--nm-ink30)] italic">{t('pages.manageAgents.untagged')}</span>
                            )}
                            {aTeams.map((tm) => (
                              <span
                                key={tm.team_id}
                                className="text-[10px] font-mono px-1.5 py-0.5 rounded-[var(--radius-sm)] border"
                                style={{
                                  borderColor: tm.color || 'var(--nm-hairline)',
                                  color: tm.color || 'var(--nm-ink70)',
                                }}
                              >
                                {tm.name}
                              </span>
                            ))}
                          </span>
                          <span className="text-[11px] text-[var(--nm-ink50)]">
                            {isImported ? (
                              <span className="text-[var(--color-warning)]">{t('pages.manageAgents.fromBundle')}</span>
                            ) : (
                              t('pages.manageAgents.createdLocally')
                            )}
                          </span>
                        </div>
                        {/* Expanded detail — grid 0fr→1fr animation (lifted
                            from the retired AgentCard). */}
                        <div
                          className={cn(
                            'grid transition-[grid-template-rows] duration-200 ease-out border-b border-[var(--nm-hairline)]',
                            isOpen ? 'grid-rows-[1fr]' : 'grid-rows-[0fr] border-b-0',
                          )}
                          aria-hidden={!isOpen}
                        >
                          <div className="overflow-hidden">
                            {owned ? (
                              <div className="px-4 py-3.5 pl-[60px] bg-[var(--nm-paper)] space-y-3">
                                {owned.verb_line && (
                                  <div className="text-[13px] leading-snug text-[var(--nm-ink70)]" data-testid="verb-line">
                                    {owned.verb_line}
                                  </div>
                                )}
                                <AttentionBanners agentId={owned.agent_id} banners={owned.attention_banners ?? []} />
                                {(owned.queue.total > 0 || owned.metrics_today.runs_ok > 0 || owned.metrics_today.errors > 0) && (
                                  <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                                    <QueueBar queue={owned.queue} compact />
                                    <MetricsRow metrics={owned.metrics_today} />
                                  </div>
                                )}
                                <div className="space-y-2 border-t border-[var(--nm-hairline)] pt-3">
                                  {owned.sessions.length > 0 && (
                                    <SessionSection agentId={owned.agent_id} sessions={owned.sessions} />
                                  )}
                                  {(owned.running_jobs.length > 0 || owned.pending_jobs.length > 0) && (
                                    <JobsSection
                                      agentId={owned.agent_id}
                                      runningJobs={owned.running_jobs}
                                      pendingJobs={owned.pending_jobs}
                                    />
                                  )}
                                  <Sparkline agentId={owned.agent_id} health={owned.health} />
                                  {owned.recent_events.length > 0 && (
                                    <RecentFeed agentId={owned.agent_id} events={owned.recent_events} />
                                  )}
                                </div>
                              </div>
                            ) : (
                              <div className="px-4 py-3 pl-[60px] bg-[var(--nm-paper)] text-[12px] text-[var(--nm-ink50)]">
                                {t('pages.dashboard.noStatusYet')}
                              </div>
                            )}
                          </div>
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
                        className="grid grid-cols-[36px_1fr_130px_140px_130px] items-center gap-0 px-3 py-2.5 border-b border-[var(--nm-hairline)] bg-[var(--nm-card)]"
                      >
                        <span />
                        <span className="flex items-center gap-2.5 min-w-0 pl-[22px]">
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
                        <span className="text-[11px] text-[var(--nm-ink50)]">{t('pages.dashboard.publicAgent')}</span>
                      </div>
                    );
                  })}
                </>
              )}
            </div>

            <p className="text-[11px] leading-relaxed text-[var(--nm-ink30)]">
              {t('pages.manageAgents.tip')}
            </p>
          </>
        )}

        {view === 'teams' && (
          <>
            <div className="rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] overflow-hidden">
              <div className="grid grid-cols-[1fr_140px_140px_100px] items-center gap-0 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--nm-ink50)] border-b border-[var(--nm-hairline)] bg-[var(--nm-paper)]">
                <span>{t('pages.dashboard.colTeam')}</span>
                <span>{t('pages.dashboard.colMembers')}</span>
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
                teams.map((tm) => (
                  <div
                    key={tm.team.team_id}
                    className="grid grid-cols-[1fr_140px_140px_100px] items-center gap-0 px-3 py-2.5 border-b border-[var(--nm-hairline)] bg-[var(--nm-card)]"
                  >
                    <span className="flex items-center gap-2.5 min-w-0">
                      <span
                        className="w-3 h-3 rounded-full shrink-0"
                        style={{ background: tm.team.color || 'var(--nm-ink30)' }}
                      />
                      <span className="text-[13px] font-semibold text-[var(--nm-ink)] truncate">{tm.team.name}</span>
                    </span>
                    <span className="text-[12px] text-[var(--nm-ink70)]">
                      {t('pages.dashboard.membersCount', { count: tm.member_agent_ids.length })}
                    </span>
                    <span className="text-[11px] text-[var(--nm-ink50)]">
                      {tm.team.source === 'bundle'
                        ? t('pages.manageAgents.fromBundle')
                        : t('pages.manageAgents.createdLocally')}
                    </span>
                    <span className="flex justify-end">
                      <Button
                        size="sm"
                        variant="outline"
                        className="gap-1.5"
                        onClick={() => setTeamsMgmtOpen(true)}
                      >
                        <Users2 className="w-3.5 h-3.5" />
                        {t('pages.dashboard.manageTeam')}
                      </Button>
                    </span>
                  </div>
                ))
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

function TableCheckbox({ checked }: { checked: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex h-3.5 w-3.5 items-center justify-center rounded-[2px] border text-[10px] leading-none',
        checked
          ? 'border-[var(--nm-ink)] bg-[var(--nm-ink)] text-[var(--nm-paper)]'
          : 'border-[var(--border-default)] bg-[var(--nm-card)] text-transparent',
      )}
      aria-checked={checked}
      role="checkbox"
    >
      ✓
    </span>
  );
}

export default DashboardPage;
