/**
 * @file_name: AgentList.tsx
 * @author:
 * @date: 2026-06-10
 * @description: The sidebar's Chats list — team rows + a flat agent list
 * with collapsible sections, per-row quick actions (rename / edit / clear /
 * delete), unread + running indicators. Creation, import and export moved
 * to the sidebar's global nav (Chat UI v4); this file owns the list only.
 */

import { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  RefreshCw,
  Plus,
  Search,
  ChevronRight,
} from 'lucide-react';
import { Button, useConfirm } from '@/components/ui';
import { BracketSectionLabel, BracketEmptyState } from '@/components/nm';
import { useConfigStore, useChatStore, useTeamsStore, useUIStore } from '@/stores';
import { useCreateAgent } from '@/hooks';
import { api } from '@/lib/api';
import { cn, formatChatTimestamp } from '@/lib/utils';
import { getLastReadMs, markAgentRead, countUnread, latestMessageMs, markTeamRead } from '@/lib/unread';
import { AgentGroupSection } from './AgentGroupSection';
import { sortAgentsByActivity } from './agentGroupUtils';
import { ClearAgentDataDialog } from './ClearAgentDataDialog';
import { EditAgentDialog } from './EditAgentDialog';
import { ClearTeamDataDialog } from '../teams/ClearTeamDataDialog';
import { TeamChatRow } from './TeamChatRow';

/**
 * Feature flag — temporary, 2026-05-18.
 * Hides the "set agent public/private" toggle button in the sidebar
 * while the public-agent feature is paused for product redesign.
 *
 * Scope of the flag:
 *  - HIDES: the per-agent Globe/Lock toggle button (set-public entry point)
 *  - KEEPS: the read-only Globe badge that marks other users' already-public
 *           agents, and the dashboard's PublicCard for displaying them —
 *           these aren't user-actionable controls, they're status indicators
 *           for legacy `is_public=1` rows that may still exist.
 *  - KEEPS: the backend endpoint and the `handleTogglePublic` function so
 *           re-enabling is a one-line flip when the feature ships again.
 *
 * Flip this to `true` to bring the toggle back. */
const SHOW_AGENT_PUBLIC_TOGGLE = false;

/** Small collapsible category header (TEAMS / AGENTS) in the sidebar list. */
function CategoryHeader({
  label,
  count,
  collapsed,
  onToggle,
}: {
  label: string;
  count: number;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="w-full flex items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-[var(--nm-paper-warm)]"
    >
      <span
        className="flex-1 min-w-0 text-[11px] font-mono uppercase tracking-wider truncate"
        style={{ color: 'var(--nm-ink50)' }}
      >
        {label}
      </span>
      <span className="text-[10px] font-mono shrink-0" style={{ color: 'var(--nm-ink30)' }}>
        {count}
      </span>
      {/* Lucide chevron, not a glyph — same linear icon language as the
          team-row toggle (design_system.md §5: no solid/linear mixing). */}
      <ChevronRight
        className={cn(
          'h-3 w-3 shrink-0 transition-transform duration-150',
          !collapsed && 'rotate-90',
        )}
        style={{ color: 'var(--nm-ink30)' }}
        aria-hidden
      />
    </button>
  );
}

export function AgentList() {
  const { t } = useTranslation();
  const [loadingAgents, setLoadingAgents] = useState(false);
  const { createAgent, creating: creatingAgent } = useCreateAgent();
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [deletingAgentId, setDeletingAgentId] = useState<string | null>(null);
  const [clearTarget, setClearTarget] = useState<typeof rawAgents[0] | null>(null);
  const [clearBusy, setClearBusy] = useState(false);
  const [editTarget, setEditTarget] = useState<typeof rawAgents[0] | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [clearTeamTarget, setClearTeamTarget] = useState<{ team_id: string; name: string } | null>(null);
  const [clearTeamBusy, setClearTeamBusy] = useState(false);
  // Collapse state for the TEAMS / AGENTS sidebar categories (persisted).
  const [teamsCollapsed, setTeamsCollapsed] = useState(
    () => typeof window !== 'undefined' && localStorage.getItem('sidebar_cat_teams') === '1',
  );
  const [agentsCollapsed, setAgentsCollapsed] = useState(
    () => typeof window !== 'undefined' && localStorage.getItem('sidebar_cat_agents') === '1',
  );
  const setCatCollapsed = (cat: 'teams' | 'agents', v: boolean) => {
    if (cat === 'teams') setTeamsCollapsed(v);
    else setAgentsCollapsed(v);
    try {
      localStorage.setItem(cat === 'teams' ? 'sidebar_cat_teams' : 'sidebar_cat_agents', v ? '1' : '0');
    } catch { /* storage unavailable — collapse just won't persist */ }
  };

  const navigate = useNavigate();
  const location = useLocation();
  const { userId, agentId, agents: rawAgents, setAgentId, setAgents, refreshAgents } = useConfigStore();
  const { setActiveAgent, clearAgent, isAgentStreaming, completedAgentIds, requestHistoryRefresh, requestWorkspaceRefresh } =
    useChatStore();
  const agentSessions = useChatStore((s) => s.agentSessions);
  const teams = useTeamsStore((s) => s.teams);
  const teamsLoaded = useTeamsStore((s) => s.loaded);
  const teamsRefresh = useTeamsStore((s) => s.refresh);
  const teamsUpdate = useTeamsStore((s) => s.updateTeam);
  const teamsDelete = useTeamsStore((s) => s.deleteTeam);
  const { confirm, alert, dialog: confirmDialog } = useConfirm();
  const setPaletteOpen = useUIStore((s) => s.setPaletteOpen);

  // Ensure teams are loaded so grouping is accurate.
  useEffect(() => {
    if (!teamsLoaded) teamsRefresh();
  }, [teamsLoaded, teamsRefresh]);

  // Mark the active agent's messages as read so its unread count stays
  // cleared after the user navigates away.
  useEffect(() => {
    if (!agentId) return;
    const latest = latestMessageMs(agentSessions[agentId]?.messages ?? []);
    if (latest > 0) markAgentRead(agentId, latest);
  }, [agentId, agentSessions]);

  /**
   * Derive the per-agent meta shown in each row: agent-reply preview +
   * activity time and an unread count.
   *
   * Preview is the most recent assistant message — NM messenger UX:
   * each row "belongs" to the agent, so the second line previews what
   * the agent last said to the user, not what the user just typed.
   */
  const getRowMeta = (aid: string) => {
    const session = agentSessions[aid];
    const messages = session?.messages ?? [];
    let sessionLast: typeof messages[number] | null = null;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].content) {
        sessionLast = messages[i];
        break;
      }
    }
    const agent = rawAgents.find((a) => a.agent_id === aid);
    const serverPreview = agent?.last_assistant_preview || '';
    const serverAtMs = agent?.last_assistant_at
      ? new Date(agent.last_assistant_at).getTime()
      : 0;

    const sessionAtMs = sessionLast?.timestamp ?? 0;
    let preview = '';
    let timeMs = 0;
    if (sessionLast && sessionAtMs >= serverAtMs) {
      preview = sessionLast.content.replace(/\s+/g, ' ').slice(0, 60);
      timeMs = sessionAtMs;
    } else if (serverPreview) {
      preview = serverPreview.replace(/\s+/g, ' ').slice(0, 60);
      timeMs = serverAtMs;
    }
    const time = timeMs ? formatChatTimestamp(timeMs) : '';
    const unread = aid !== agentId ? countUnread(messages, getLastReadMs(aid)) : 0;
    return { preview, time, unread };
  };

  const getIsStreaming = (aid: string) => isAgentStreaming(aid);

  /**
   * Cheap per-render projection of ONLY what can change sort order: each
   * agent's id + committed-message count + last message time. Streaming
   * deltas rebuild the `agentSessions` object every token but mutate
   * `currentEvents` / `currentAssistantMessage`, NOT `messages` (see
   * chatStore.updateSession) — so this string is byte-identical across the
   * per-token churn. It's O(n) (reads length + tail element, no full scan)
   * and gates the O(n·m) sort below to re-run only when a message is actually
   * committed. Long sessions (铁律 #14) make the avoided work grow, and 铁律
   * #16 says the platform must not become the interruption source: keeping
   * the sidebar off the streaming hot path honors both.
   */
  const activitySignature = rawAgents
    .map((a) => {
      const msgs = agentSessions[a.agent_id]?.messages;
      const last = msgs && msgs.length ? msgs[msgs.length - 1] : undefined;
      return `${a.agent_id}:${msgs?.length ?? 0}:${last?.timestamp ?? 0}`;
    })
    .join('|');

  /**
   * Agents ordered so the most-recently-active conversation floats to the top
   * ("recently chatted agent auto-pins"). The activity time blends the
   * server's last assistant reply with the freshest LOCAL session message, so
   * an agent jumps to the top the instant you talk to it — before the next
   * /api/auth/agents refresh.
   */
  const sortedAgents = useMemo(
    () =>
      sortAgentsByActivity(rawAgents, (aid) =>
        latestMessageMs(agentSessions[aid]?.messages ?? []),
      ),
    // agentSessions is intentionally NOT a dep: activitySignature is its
    // sort-relevant projection. The closure still reads the current render's
    // agentSessions, which is fresh on every render where the signature (and
    // therefore the sort result) could have changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rawAgents, activitySignature],
  );

  // Fetch agents on mount
  useEffect(() => {
    fetchAgents();
  }, []);

  const fetchAgents = async () => {
    setLoadingAgents(true);
    try {
      await refreshAgents();
      const currentAgents = useConfigStore.getState().agents;
      if (!agentId && currentAgents.length > 0) {
        setAgentId(currentAgents[0].agent_id);
      }
    } catch (err) {
      console.error('Failed to fetch agents:', err);
    } finally {
      setLoadingAgents(false);
    }
  };

  const handleSelectAgent = (id: string) => {
    if (id !== agentId) {
      setAgentId(id);
      setActiveAgent(id);
    }
    if (location.pathname !== '/app/chat' && location.pathname !== '/app') {
      navigate('/app/chat');
    }
  };

  const handleCreateAgent = async () => {
    await createAgent();
  };

  // #43: create a new agent already assigned to this team, then open the
  // team's group chat so the membership change is immediately visible. The
  // entry point now lives in the TEAMS-row ⋮ menu (TeamRowMenu) since the old
  // AgentGroupSection-header "+" no longer exists in the TEAMS/AGENTS layout.
  const handleCreateAgentInTeam = async (teamId: string) => {
    const id = await createAgent({ teamId });
    if (id) navigate(`/app/teams/${teamId}/chat`);
  };

  const handleTogglePublic = async (agent: typeof rawAgents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    const newIsPublic = !agent.is_public;
    try {
      const res = await api.updateAgent(agent.agent_id, undefined, undefined, newIsPublic);
      if (res.success) {
        // Same contract as the two rename paths: optimistic paint, then
        // invalidate. Reached only when SHOW_AGENT_PUBLIC_TOGGLE is flipped
        // back on — and that flip is a one-line change, so this is kept in
        // step rather than left as a copy of the pattern this PR removed.
        setAgents(rawAgents.map(a =>
          a.agent_id === agent.agent_id ? { ...a, is_public: newIsPublic } : a
        ));
        await refreshAgents();
      } else {
        console.error('Failed to toggle public:', res.error);
        await alert({
          title: t('layout.editAgentDialog.saveFailedTitle'),
          message: res.error || 'Failed to update agent',
          danger: true,
        });
      }
    } catch (err) {
      console.error('Error toggling public:', err);
      await alert({
        title: t('layout.editAgentDialog.saveFailedTitle'),
        message: String(err),
        danger: true,
      });
    }
  };

  const handleStartEdit = (agent: typeof rawAgents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingAgentId(agent.agent_id);
    setEditingName(agent.name || agent.agent_id);
  };

  const handleEditAgent = (agent: typeof rawAgents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    setEditTarget(agent);
  };

  const doEditAgent = async (name: string, description: string) => {
    if (!editTarget) return;
    setEditBusy(true);
    try {
      const res = await api.updateAgent(editTarget.agent_id, name, description);
      if (res.success && res.agent) {
        // Paint the server's own values immediately, then re-read the list so
        // the persisted copy is server truth and not a locally-patched object.
        // `agents` is persisted to localStorage with no `partialize`, so a
        // hand-patched row is what a later page load would show — which is how
        // a rename could appear to revert.
        //
        // The two fields take the response differently ON PURPOSE; do not
        // "unify" them. `description` is taken as-is (`?? ''`): the response
        // always carries the key, and a cleared description comes back as ''
        // or null — falling back to the local value there would put the text
        // the user just deleted back into the persisted store. `name` keeps a
        // local fallback because it is the row title and a null would render
        // the raw agent_id.
        setAgents(rawAgents.map(a =>
          a.agent_id === editTarget.agent_id
            ? { ...a, name: res.agent?.name ?? a.name, description: res.agent?.description ?? '' }
            : a
        ));
        setEditTarget(null);
        await refreshAgents();
      } else {
        await alert({
          title: t('layout.editAgentDialog.saveFailedTitle'),
          message: res.error || 'Failed to update agent',
          danger: true,
        });
      }
    } catch (err) {
      await alert({
        title: t('layout.editAgentDialog.saveFailedTitle'),
        message: String(err),
        danger: true,
      });
    } finally {
      setEditBusy(false);
    }
  };

  const handleCancelEdit = (e: React.SyntheticEvent) => {
    e.stopPropagation();
    setEditingAgentId(null);
    setEditingName('');
  };

  const handleSaveEdit = async (targetAgentId: string, e: React.SyntheticEvent) => {
    e.stopPropagation();
    if (!editingName.trim()) return;

    setSavingName(true);
    try {
      const res = await api.updateAgent(targetAgentId, editingName.trim());
      if (res.success && res.agent) {
        // Same contract as doEditAgent: optimistic paint, then invalidate.
        setAgents(rawAgents.map(a =>
          a.agent_id === targetAgentId
            ? { ...a, name: res.agent?.name ?? a.name }
            : a
        ));
        setEditingAgentId(null);
        setEditingName('');
        await refreshAgents();
      } else {
        // A rename that did not happen must say so. Console-only meant the row
        // silently snapped back to the old name, which the user reads as "the
        // platform lost my edit" — and retries, learning nothing each time.
        console.error('Failed to update agent:', res.error);
        await alert({
          title: t('layout.editAgentDialog.saveFailedTitle'),
          message: res.error || 'Failed to update agent',
          danger: true,
        });
      }
    } catch (err) {
      console.error('Error updating agent:', err);
      await alert({
        title: t('layout.editAgentDialog.saveFailedTitle'),
        message: String(err),
        danger: true,
      });
    } finally {
      setSavingName(false);
    }
  };

  const handleDeleteAgent = async (agent: typeof rawAgents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    const ok = await confirm({
      title: t('layout.agentList.deleteAgentTitle'),
      message: t('layout.agentList.deleteAgentMessage', { name: agent.name || agent.agent_id }),
      confirmText: t('layout.agentList.deleteAction'),
      danger: true,
    });
    if (!ok) return;

    setDeletingAgentId(agent.agent_id);
    try {
      const res = await api.deleteAgent(agent.agent_id);
      if (res.success) {
        const remaining = rawAgents.filter(a => a.agent_id !== agent.agent_id);
        setAgents(remaining);
        clearAgent(agent.agent_id);
        if (agentId === agent.agent_id) {
          if (remaining.length > 0) {
            setAgentId(remaining[0].agent_id);
            setActiveAgent(remaining[0].agent_id);
          } else {
            setAgentId('');
          }
        }
      } else {
        console.error('Failed to delete agent:', res.error);
        await alert({
          title: t('layout.agentList.deleteFailedTitle'),
          message: t('layout.agentList.deleteAgentFailedMessage', { error: res.error }),
          danger: true,
        });
      }
    } catch (err) {
      console.error('Error deleting agent:', err);
      await alert({
        title: t('layout.agentList.deleteFailedTitle'),
        message: t('layout.agentList.deleteAgentError'),
        danger: true,
      });
    } finally {
      setDeletingAgentId(null);
    }
  };

  const handleClearData = (agent: typeof rawAgents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    setClearTarget(agent);
  };

  const doClearData = async (scopes: { conversations: boolean; memory: boolean }) => {
    if (!clearTarget) return;
    setClearBusy(true);
    try {
      const res = await api.clearHistory(clearTarget.agent_id, scopes);
      if (res.success) {
        // Drop the in-memory session AND force any mounted ChatPanel to
        // re-fetch server history (which is now empty) so the view doesn't
        // keep showing stale messages without a manual refresh.
        if (scopes.conversations) {
          clearAgent(clearTarget.agent_id);
          requestHistoryRefresh();
        }
        if (res.disk_errors?.length) {
          await alert({
            title: t('layout.clearAgentData.title', { name: clearTarget.name || clearTarget.agent_id }),
            message: t('layout.clearAgentData.toastDiskWarn', { count: res.disk_errors.length }),
            danger: true,
          });
        }
      } else {
        await alert({
          title: t('layout.agentList.deleteFailedTitle'),
          message: res.error || 'Failed to clear agent data',
          danger: true,
        });
      }
    } catch (err) {
      console.error('Error clearing agent data:', err);
      await alert({
        title: t('layout.agentList.deleteFailedTitle'),
        message: String(err),
        danger: true,
      });
    } finally {
      setClearBusy(false);
      setClearTarget(null);
    }
  };

  const doClearTeamData = async (scopes: { chat: boolean; files: boolean; bulletin: boolean }) => {
    if (!clearTeamTarget) return;
    setClearTeamBusy(true);
    try {
      const res = await api.clearTeamData(clearTeamTarget.team_id, scopes);
      if (res.success) {
        if (scopes.chat) requestHistoryRefresh();
        // The files scope also takes the team's artifacts with it (they live
        // in that folder), and an open workspace panel would otherwise keep
        // listing both until the next message arrives. The bulletin panel
        // reloads off the same tick, so a cleared bulletin does not sit on
        // screen listing rules the server has already deleted.
        if (scopes.files || scopes.bulletin) requestWorkspaceRefresh();
      } else {
        await alert({
          title: t('layout.agentList.deleteFailedTitle'),
          message: res.error || 'Failed to clear team data',
          danger: true,
        });
      }
    } catch (err) {
      console.error('Error clearing team data:', err);
      await alert({ title: t('layout.agentList.deleteFailedTitle'), message: String(err), danger: true });
    } finally {
      setClearTeamBusy(false);
      setClearTeamTarget(null);
    }
  };

  const handleDeleteTeam = async (teamId: string) => {
    const team = teams.find((x) => x.team.team_id === teamId);
    const ok = await confirm({
      title: t('layout.agentList.deleteTeamTitle'),
      message: t('layout.agentList.deleteTeamMessage', { name: team?.team.name ?? teamId }),
      confirmText: t('layout.agentList.deleteAction'),
      danger: true,
    });
    if (!ok) return;
    try {
      await teamsDelete(teamId);
      // If the deleted team's chat/detail is open, fall back to the chat view.
      if (location.pathname.startsWith(`/app/teams/${teamId}`)) {
        navigate('/app/chat');
      }
    } catch (err) {
      await alert({
        title: t('layout.agentList.deleteFailedTitle'),
        message: err instanceof Error ? err.message : String(err),
        danger: true,
      });
    }
  };

  // Which team's group chat is open (route /app/teams/:id/chat) — drives the
  // active highlight on the Group chat row and suppresses agent-row selection.
  const teamChatMatch = location.pathname.match(/^\/app\/teams\/([^/]+)\/chat$/);
  const activeTeamChatId = teamChatMatch ? teamChatMatch[1] : null;

  // Opening a room clears its mark DURABLY: the watermark goes to localStorage,
  // so it stays cleared after navigating away (dev's team-unread logic, merged
  // 2026-08-18). The v4 team row currently shows no unread dot — Owner ruling
  // kept the v4 row layout — but the watermark is maintained so wiring a dot
  // back is a one-prop change, and the panel's own advancing stays monotonic
  // with this one.
  const activeTeamLastMessageAt =
    teams.find((t) => t.team.team_id === activeTeamChatId)?.last_message_at ?? null;
  useEffect(() => {
    if (!activeTeamChatId || !activeTeamLastMessageAt) return;
    // An unparseable timestamp is NaN, which markTeamRead already refuses.
    markTeamRead(activeTeamChatId, Date.parse(activeTeamLastMessageAt));
  }, [activeTeamChatId, activeTeamLastMessageAt]);

  return (
    <div>
      {confirmDialog}
      {clearTarget && (
        <ClearAgentDataDialog
          agentName={clearTarget.name || clearTarget.agent_id}
          busy={clearBusy}
          onCancel={() => setClearTarget(null)}
          onConfirm={doClearData}
        />
      )}
      {editTarget && (
        <EditAgentDialog
          initialName={editTarget.name || ''}
          initialDescription={editTarget.description || ''}
          busy={editBusy}
          onCancel={() => setEditTarget(null)}
          onSave={doEditAgent}
        />
      )}
      {clearTeamTarget && (
        <ClearTeamDataDialog
          teamName={clearTeamTarget.name}
          busy={clearTeamBusy}
          onCancel={() => setClearTeamTarget(null)}
          onConfirm={doClearTeamData}
        />
      )}
      {/* Header — v4: label + count with search (⌘K palette) and refresh.
          Creation / import / export moved to the sidebar's global nav. */}
      <div className="sticky top-0 z-10 bg-[color:var(--nm-paper)] px-3 pt-2.5 pb-1.5">
        <div className="flex items-center justify-between px-1 gap-2">
          <span data-help-id="sidebar.agent-list">
            <BracketSectionLabel
              trailing={<span className="text-[10px] opacity-60">{teams.length + rawAgents.length}</span>}
            >
              {t('sidebar.chats')}
            </BracketSectionLabel>
          </span>
          <div className="flex items-center gap-1 shrink-0">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setPaletteOpen(true)}
              className="w-7 h-7"
              title={t('sidebar.searchChatsTitle')}
              aria-label={t('sidebar.searchChatsTitle')}
            >
              <Search className="w-3.5 h-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={fetchAgents}
              disabled={loadingAgents}
              className="w-7 h-7"
              title={t('layout.agentList.refreshAgents')}
            >
              <RefreshCw className={cn('w-3 h-3', loadingAgents && 'animate-spin')} />
            </Button>
          </div>
        </div>
      </div>

      <div className="px-1 pb-3">
        {rawAgents.length === 0 && teams.length === 0 ? (
          <BracketEmptyState
            label={t('layout.agentList.emptyLabel')}
            hint={t('layout.agentList.emptyHint')}
            cta={
              <Button
                variant="outline"
                size="sm"
                onClick={handleCreateAgent}
                disabled={creatingAgent}
                className="gap-1.5"
              >
                <Plus className="w-3.5 h-3.5" />
                {t('layout.agentList.createAgent')}
              </Button>
            }
          />
        ) : (
          <div className="space-y-1">
            {/* TEAMS — group chats, collected at the top (one row per team). */}
            {teams.length > 0 && (
              <div>
                <CategoryHeader
                  label={t('sidebar.teams')}
                  count={teams.length}
                  collapsed={teamsCollapsed}
                  onToggle={() => setCatCollapsed('teams', !teamsCollapsed)}
                />
                {!teamsCollapsed && (
                  <div className="space-y-0.5 px-1 pb-1">
                    {teams.map((t) => (
                      <TeamChatRow
                        key={t.team.team_id}
                        teamId={t.team.team_id}
                        teamName={t.team.name}
                        agentCount={t.member_agent_ids.length}
                        active={activeTeamChatId === t.team.team_id}
                        members={t.member_agent_ids.map((aid) => ({
                          agentId: aid,
                          name: rawAgents.find((a) => a.agent_id === aid)?.name || aid,
                        }))}
                        activeAgentId={activeTeamChatId ? null : agentId}
                        onSelectMember={handleSelectAgent}
                        onOpen={(tid) => navigate(`/app/teams/${tid}/chat`)}
                        onRename={(tid, name) => { void teamsUpdate(tid, { name }); }}
                        onDelete={handleDeleteTeam}
                        onClearData={(tid) => setClearTeamTarget({ team_id: tid, name: t.team.name })}
                        onAddAgent={handleCreateAgentInTeam}
                        addingAgent={creatingAgent}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* AGENTS — every agent once, flat (no per-team duplication). */}
            <div>
              <CategoryHeader
                label={t('sidebar.agents')}
                count={rawAgents.length}
                collapsed={agentsCollapsed}
                onToggle={() => setCatCollapsed('agents', !agentsCollapsed)}
              />
              {!agentsCollapsed && (
                rawAgents.length === 0 ? (
                  <div className="px-3 py-2 text-xs" style={{ color: 'var(--nm-ink50)' }}>
                    {t('layout.agentList.noAgentsShort')}
                  </div>
                ) : (
                  <AgentGroupSection
                    teamId={null}
                    teamName=""
                    teamColor={null}
                    agents={sortedAgents}
                    agentId={agentId}
                    activeTeamChatId={activeTeamChatId}
                    collapsed={false}
                    hideHeader
                    onToggleCollapse={() => {}}
                    onSelectAgent={handleSelectAgent}
                    getRowMeta={getRowMeta}
                    getIsStreaming={getIsStreaming}
                    completedAgentIds={completedAgentIds}
                    currentUserId={userId}
                    showPublicToggle={SHOW_AGENT_PUBLIC_TOGGLE}
                    editingAgentId={editingAgentId}
                    editingName={editingName}
                    onEditNameChange={setEditingName}
                    onSaveEdit={handleSaveEdit}
                    onCancelEdit={handleCancelEdit}
                    savingName={savingName}
                    onStartEdit={handleStartEdit}
                    onEditAgent={handleEditAgent}
                    onClearData={handleClearData}
                    onDelete={handleDeleteAgent}
                    onTogglePublic={handleTogglePublic}
                    deletingAgentId={deletingAgentId}
                  />
                )
              )}
            </div>
          </div>
        )}
      </div>

    </div>
  );
}
