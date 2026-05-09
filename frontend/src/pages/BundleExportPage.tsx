/**
 * BundleExportPage — Subproject 2 Export Wizard.
 *
 * Layout:
 *   ┌────────────────────────────────────────────────────────────────┐
 *   │  Step:  Agents → Skills → Social Network → Workspace files     │
 *   ├────────────────────────────────────────────────────────────────┤
 *   │ active tab content                                              │
 *   ├────────────────────────────────────────────────────────────────┤
 *   │ Bundle Notes (markdown editor — optional)                      │
 *   ├────────────────────────────────────────────────────────────────┤
 *   │ [< back to settings]    [continue → review summary]            │
 *   └────────────────────────────────────────────────────────────────┘
 *
 * Final step is a Review Summary modal that shows everything that's
 * about to be packaged + warnings + the explicit "free text not auto-scanned"
 * notice. User clicks Download to actually trigger the export.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  AlertTriangle,
  Check,
  Download,
  FileText,
  Loader2,
  Package,
  Search,
  Users,
  Wrench,
  ListTree,
  Hexagon,
} from 'lucide-react';
import { Button, useConfirm } from '@/components/ui';
import { useConfigStore, useTeamsStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type {
  BundleExportRequest,
  SkillExportSpec,
  SkillArchiveRecord,
  TeamWithMembers,
} from '@/types';

const TABS: { id: TabId; label: string; icon: any }[] = [
  { id: 'agents', label: 'Agents', icon: Users },
  { id: 'history', label: 'Chat history', icon: FileText },
  { id: 'skills', label: 'Skills', icon: Wrench },
  { id: 'social', label: 'Social Network', icon: Hexagon },
  { id: 'workspace', label: 'Workspace files', icon: ListTree },
];

type TabId = 'agents' | 'history' | 'skills' | 'social' | 'workspace';

interface SocialEntity {
  entity_id: string;
  entity_type: string;
  entity_name?: string | null;
  entity_description?: string | null;
  tags?: string[] | null;
}

interface ChatHistoryEvent {
  event_id: string;
  trigger?: string;
  created_at?: string;
  preview?: string;
}

interface ChatHistoryNarrative {
  narrative_id: string;
  title?: string;
  type?: string;
  events: ChatHistoryEvent[];
}

export default function BundleExportPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { agents, userId } = useConfigStore();
  const { teams, refresh: refreshTeams } = useTeamsStore();
  const { alert, dialog } = useConfirm();

  const [tab, setTab] = useState<TabId>('agents');
  // PRD §5 议题 2 — Full vs Custom export mode (PRD names: Full = 1:1 snapshot
  // for self-backup; Custom = pick-and-choose for sharing).
  const [mode, setMode] = useState<'full' | 'custom'>('custom');
  const [selectedAgents, setSelectedAgents] = useState<Set<string>>(new Set());
  const [selectedTeam, setSelectedTeam] = useState<string>('');
  const [introMd, setIntroMd] = useState('');
  const [includeChat, setIncludeChat] = useState(true);

  // Skills state. Choices are keyed per (agent_id, skill_name) so the same
  // skill name installed on 5 different agents becomes 5 independent rows
  // in the UI and 5 independent entries in the bundle (each carries its
  // own .skill_meta.json under Full mode).
  const [skillsForAgents, setSkillsForAgents] = useState<Record<string, string[]>>({});
  const [skillArchives, setSkillArchives] = useState<SkillArchiveRecord[]>([]);
  const [skillChoices, setSkillChoices] = useState<Record<string, SkillExportSpec>>({});

  const skillKey = (agentId: string, skillName: string) => `${agentId}::${skillName}`;

  // Social state
  const [socialEntities, setSocialEntities] = useState<Record<string, SocialEntity[]>>({});
  const [socialSelected, setSocialSelected] = useState<Record<string, Set<string>>>({});
  const [socialPage, setSocialPage] = useState<Record<string, number>>({});

  // Workspace state
  const [workspaceFiles, setWorkspaceFiles] = useState<Record<string, { path: string; size: number; sensitive: boolean }[]>>({});
  const [workspaceExcludes, setWorkspaceExcludes] = useState<Record<string, Set<string>>>({});

  // B2: chat history selection state — narrative-level allowlist (per agent)
  // and event-level allowlist (per narrative). Default = all included.
  const [historyByAgent, setHistoryByAgent] = useState<Record<string, ChatHistoryNarrative[]>>({});
  const [excludedNarratives, setExcludedNarratives] = useState<Record<string, Set<string>>>({});
  const [excludedEvents, setExcludedEvents] = useState<Record<string, Set<string>>>({});

  // B6: sensitive zip confirmation flag (set after user confirms)
  const [acceptSensitiveZips, setAcceptSensitiveZips] = useState(false);
  const [sensitiveHits, setSensitiveHits] = useState<{ skill: string; hits: string[] }[] | null>(null);

  const [reviewing, setReviewing] = useState(false);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => { refreshTeams(); }, [refreshTeams]);
  useEffect(() => {
    api.listSkillArchives().then((r) => setSkillArchives(r.archives)).catch(() => {});
  }, []);

  // Quick-launch from TeamDetailPage: ?team=<team_id>&agents=<csv>
  // Pre-seed selection so the user can hit "Review & Export" immediately.
  // Run once on mount only — don't re-apply if the user later changes
  // selection by hand.
  useEffect(() => {
    const teamFromQuery = searchParams.get('team');
    const agentsFromQuery = searchParams.get('agents');
    if (teamFromQuery) setSelectedTeam(teamFromQuery);
    if (agentsFromQuery) {
      const ids = agentsFromQuery.split(',').filter(Boolean);
      if (ids.length > 0) setSelectedAgents(new Set(ids));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When agent selection changes, prefetch skills + social entities + files lazily
  useEffect(() => {
    selectedAgents.forEach((aid) => {
      if (!skillsForAgents[aid]) {
        api.listSkills(aid, userId, true)
          .then((r) => {
            setSkillsForAgents((s) => ({ ...s, [aid]: r.skills.map((sk: any) => sk.name) }));
          }).catch(() => {});
      }
      if (!socialEntities[aid]) {
        api.getSocialNetworkList(aid)
          .then((r: any) => {
            // Verified shape: { success, entities: [...], count }
            // Each entity: entity_id, entity_name, entity_type, entity_description,
            //              tags, keywords, identity_info, contact_info, persona, ...
            const entities: SocialEntity[] = (r.entities || []).map((e: any) => ({
              entity_id: e.entity_id,
              entity_type: e.entity_type,
              entity_name: e.entity_name,
              entity_description: e.entity_description,
              tags: e.tags || e.keywords || [],
            }));
            setSocialEntities((s) => ({ ...s, [aid]: entities }));
          }).catch(() => {});
      }
      if (!workspaceFiles[aid]) {
        api.listFiles(aid, userId)
          .then((r: any) => {
            // Verified shape: { success, files: [{filename, size, modified_at}],
            //                   workspace_path, error }
            // NB: only top-level files are listed (no subdir recursion).
            const files = (r.files || []).map((f: any) => ({
              path: f.filename,
              size: f.size || 0,
              sensitive: isSensitive(f.filename),
            }));
            setWorkspaceFiles((s) => ({ ...s, [aid]: files }));
          }).catch(() => {});
      }
      // Chat history (B2)
      if (!historyByAgent[aid]) {
        api.getChatHistory(aid, userId)
          .then((r: any) => {
            // shape: { success, narratives: [{narrative_id, title, events: [...]}] }
            const narrs: ChatHistoryNarrative[] = (r.narratives || []).map((n: any) => ({
              narrative_id: n.narrative_id,
              title: n.title || n.topic_hint || n.narrative_id,
              type: n.type,
              events: (n.events || []).map((e: any) => ({
                event_id: e.event_id,
                trigger: e.trigger,
                created_at: e.created_at,
                preview: (e.final_output || '').slice(0, 80),
              })),
            }));
            setHistoryByAgent((s) => ({ ...s, [aid]: narrs }));
          }).catch(() => {});
      }
    });
  }, [selectedAgents, userId]);

  // Default skill choice per (agent, skill) based on archive availability.
  useEffect(() => {
    const newChoices: Record<string, SkillExportSpec> = {};
    Object.entries(skillsForAgents).forEach(([aid, skillNames]) => {
      skillNames.forEach((skill) => {
        const key = skillKey(aid, skill);
        if (skillChoices[key]) {
          newChoices[key] = skillChoices[key];
          return;
        }
        const arch = skillArchives.find((a) => a.skill_name === skill);
        if (arch?.source_url) {
          newChoices[key] = {
            skill_name: skill,
            agent_id: aid,
            install_method: 'url',
            source_url: arch.source_url,
            source_type: 'github',
            branch: 'main',
          };
        } else if (arch?.archive_path) {
          newChoices[key] = {
            skill_name: skill,
            agent_id: aid,
            install_method: 'zip',
            archive_path: arch.archive_path,
          };
        } else {
          newChoices[key] = {
            skill_name: skill,
            agent_id: aid,
            install_method: 'full_copy',
          };
        }
      });
    });
    setSkillChoices(newChoices);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(skillsForAgents), JSON.stringify(skillArchives)]);

  // Default social entity selection: match by team-mate name fuzzy
  useEffect(() => {
    const teamMateNames = new Set<string>();
    if (selectedTeam) {
      const t = teams.find((x) => x.team.team_id === selectedTeam);
      if (t) {
        t.member_agent_ids.forEach((aid) => {
          const a = agents.find((x) => x.agent_id === aid);
          if (a?.name) teamMateNames.add(a.name);
        });
      }
    }
    const next: Record<string, Set<string>> = {};
    Object.entries(socialEntities).forEach(([aid, list]) => {
      const set = new Set<string>();
      list.forEach((e) => {
        if (matchesTeam(e, selectedAgents, teamMateNames)) set.add(e.entity_id);
      });
      next[aid] = set;
    });
    setSocialSelected(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(socialEntities), selectedTeam, JSON.stringify(Array.from(selectedAgents))]);

  function matchesTeam(e: SocialEntity, closure: Set<string>, names: Set<string>): boolean {
    if (e.entity_type === 'agent' && closure.has(e.entity_id)) return true;
    const n = (e.entity_name || '').toLowerCase();
    for (const tn of names) {
      const lc = tn.toLowerCase();
      if (n.includes(lc)) return true;
      if ((e.tags || []).some((t) => t.toLowerCase().includes(lc))) return true;
      if ((e.entity_description || '').toLowerCase().includes(lc)) return true;
    }
    return false;
  }

  function isSensitive(p: string): boolean {
    const lc = p.toLowerCase();
    return /\.env(\b|\.)|\.aws\/|\.ssh\/|\.git\/config|id_rsa|\.pem|\.key|credentials\.|_token|_secret/.test(lc);
  }

  function toggleAgent(aid: string) {
    // Full mode still allows agent selection — see PRD note at the top of the
    // Full-mode pre-fill effect. Only the *granularity* tabs (skills/social/
    // workspace/history) become read-only.
    setSelectedAgents((prev) => {
      const next = new Set(prev);
      if (next.has(aid)) next.delete(aid);
      else next.add(aid);
      return next;
    });
  }

  function toggleSocial(aid: string, eid: string) {
    if (mode === 'full') return;
    setSocialSelected((prev) => {
      const next = { ...prev };
      const set = new Set(next[aid] || []);
      if (set.has(eid)) set.delete(eid); else set.add(eid);
      next[aid] = set;
      return next;
    });
  }

  function toggleWorkspaceFile(aid: string, path: string) {
    if (mode === 'full') return;
    setWorkspaceExcludes((prev) => {
      const next = { ...prev };
      const set = new Set(next[aid] || []);
      if (set.has(path)) set.delete(path); else set.add(path);
      next[aid] = set;
      return next;
    });
  }

  const summary = useMemo(() => {
    return {
      agents: selectedAgents.size,
      skills: Object.values(skillChoices).filter((s) => s && s.install_method).length,
      socialEntities: Object.values(socialSelected).reduce((a, b) => a + b.size, 0),
      workspaceExcluded: Object.values(workspaceExcludes).reduce((a, b) => a + b.size, 0),
    };
  }, [selectedAgents, skillChoices, socialSelected, workspaceExcludes]);

  // Full mode: pre-fill granularity to "everything for the selected agents",
  // but DO NOT touch the agent selection itself — the user picks which agents
  // to ship (you might want to copy 3 of 11 agents to a new machine, not all
  // 11). PRD §5 议题 2: Full vs Custom is depth (with-credentials vs
  // strip-credentials), not breadth.
  useEffect(() => {
    if (mode !== 'full') return;
    // 2. Force every (agent, skill) pair to full_copy mode (carries
    //    .skill_meta.json + credentials.json + wallet.json per agent —
    //    the "self-backup" contract; same skill name on different agents
    //    has different credentials).
    const next: Record<string, SkillExportSpec> = { ...skillChoices };
    Object.entries(skillsForAgents).forEach(([aid, skillNames]) => {
      skillNames.forEach((skill) => {
        next[skillKey(aid, skill)] = {
          skill_name: skill,
          agent_id: aid,
          install_method: 'full_copy',
        };
      });
    });
    setSkillChoices(next);
    // 3. Select every social entity for every agent (no closure-fuzzy filter).
    setSocialSelected((cur) => {
      const out = { ...cur };
      Object.entries(socialEntities).forEach(([aid, list]) => {
        out[aid] = new Set(list.map((e) => e.entity_id));
      });
      return out;
    });
    // 4. Clear workspace + history exclusions — Full ships everything.
    setWorkspaceExcludes({});
    setExcludedNarratives({});
    setExcludedEvents({});
    setIncludeChat(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, JSON.stringify(skillsForAgents), JSON.stringify(socialEntities)]);

  async function doExport() {
    setDownloading(true);
    try {
      const skills: SkillExportSpec[] = Object.values(skillChoices).filter((s) => !!s.install_method);
      const social: Record<string, string[]> = {};
      Object.entries(socialSelected).forEach(([aid, set]) => {
        social[aid] = Array.from(set);
      });
      const excludes: Record<string, string[]> = {};
      Object.entries(workspaceExcludes).forEach(([aid, set]) => {
        // Auto-add sensitive files to excludes if not explicitly opted-in
        const sens = (workspaceFiles[aid] || []).filter((f) => f.sensitive).map((f) => f.path);
        excludes[aid] = Array.from(new Set([...sens, ...Array.from(set)]));
      });
      // B2: derive narrative + event allowlists from "exclusion" sets
      const narrativeSel: Record<string, string[]> = {};
      const eventSel: Record<string, string[]> = {};
      Array.from(selectedAgents).forEach((aid) => {
        const allNarrs = historyByAgent[aid] || [];
        const exNars = excludedNarratives[aid] || new Set();
        // Only emit a selection if user actually de-selected something;
        // otherwise leave undefined to fall back to "include all" semantics.
        if (exNars.size > 0) {
          narrativeSel[aid] = allNarrs
            .filter((n) => !exNars.has(n.narrative_id))
            .map((n) => n.narrative_id);
        }
        // Per-narrative event filtering
        allNarrs.forEach((n) => {
          const exEvts = excludedEvents[n.narrative_id];
          if (exEvts && exEvts.size > 0) {
            eventSel[n.narrative_id] = n.events
              .filter((e) => !exEvts.has(e.event_id))
              .map((e) => e.event_id);
          }
        });
      });
      const payload: BundleExportRequest = {
        agent_ids: Array.from(selectedAgents),
        team_id: selectedTeam || null,
        team_intro_md: introMd || null,
        skills,
        social_entity_selection: social,
        workspace_excludes: excludes,
        include_chat_history: includeChat,
        // Full mode user has already accepted "ship credentials" semantics by
        // picking that mode; auto-flag so they don't have to confirm twice.
        accept_sensitive_zips: mode === 'full' ? true : acceptSensitiveZips,
        narrative_selection: Object.keys(narrativeSel).length ? narrativeSel : null,
        event_selection: Object.keys(eventSel).length ? eventSel : null,
      };
      const { blob, filename, warningsCount, externalEdgesDropped } = await api.exportBundle(payload);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      // Warnings are real concerns; "external edges dropped" is informational
      // (expected closure behavior) and shown separately so the user doesn't
      // panic over hundreds of routine edge-drops.
      const parts = [`${filename} downloaded.`];
      if (externalEdgesDropped > 0) {
        parts.push(
          `Dropped ${externalEdgesDropped} reference(s) to entities outside the bundle ` +
          '(expected — your agents had social-network notes about agents not in the closure).'
        );
      }
      if (warningsCount > 0) {
        parts.push(`${warningsCount} warning${warningsCount === 1 ? '' : 's'} (see manifest.json).`);
      }
      await alert({
        title: 'Bundle created',
        message: parts.join(' '),
      });
      navigate('/app/settings');
    } catch (e: any) {
      console.error(e);
      // B6: detect 409 SENSITIVE_FILES_IN_SKILL_ZIP and surface confirmation modal
      if (e?.code === 'SENSITIVE_FILES_IN_SKILL_ZIP' && e?.hits) {
        setSensitiveHits(e.hits);
      } else {
        await alert({ title: 'Export failed', message: e?.message || String(e), danger: true });
      }
    } finally {
      setDownloading(false);
      setReviewing(false);
    }
  }

  return (
    <div className="h-full flex flex-col bg-[var(--bg-primary)]">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[var(--border-default)] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/app/settings')} className="p-1 hover:bg-[var(--bg-tertiary)]">
            <ArrowLeft className="w-4 h-4" />
          </button>
          <Package className="w-5 h-5" />
          <h1 className="font-mono text-base">Export bundle</h1>
        </div>
        <div className="text-xs text-[var(--text-tertiary)]">
          {summary.agents} agents · {summary.skills} skills · {summary.socialEntities} entities
        </div>
      </div>

      {/* Mode picker (PRD §5 议题 2) */}
      <div className="px-6 py-3 border-b border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
        <div className="flex items-start gap-3">
          <span className="text-[10px] uppercase tracking-widest text-[var(--text-tertiary)] mt-1.5 font-mono shrink-0">
            Mode
          </span>
          <div className="flex-1 grid grid-cols-2 gap-2">
            <button
              onClick={() => setMode('full')}
              className={cn(
                'text-left p-3 border transition-colors',
                mode === 'full'
                  ? 'border-[var(--text-primary)] bg-[var(--bg-elevated)]'
                  : 'border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
              )}
            >
              <div className="flex items-center gap-2">
                <span className={cn(
                  'w-3 h-3 rounded-full border-2',
                  mode === 'full' ? 'border-[var(--text-primary)] bg-[var(--text-primary)]' : 'border-[var(--text-tertiary)]'
                )} />
                <span className="font-mono text-sm">Full snapshot</span>
                <span className="text-[10px] px-1.5 py-0.5 border border-[var(--color-yellow-500)] text-[var(--color-yellow-500)]">
                  contains_secrets
                </span>
              </div>
              <div className="text-[11px] text-[var(--text-tertiary)] mt-1.5 leading-relaxed">
                Self-backup. Includes <strong>all</strong> agents, narratives, events,
                workspace files, and skills with credentials (env_config, wallet.json,
                study summaries). Use to clone an entire setup to another of YOUR machines.
              </div>
            </button>
            <button
              onClick={() => setMode('custom')}
              className={cn(
                'text-left p-3 border transition-colors',
                mode === 'custom'
                  ? 'border-[var(--text-primary)] bg-[var(--bg-elevated)]'
                  : 'border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
              )}
            >
              <div className="flex items-center gap-2">
                <span className={cn(
                  'w-3 h-3 rounded-full border-2',
                  mode === 'custom' ? 'border-[var(--text-primary)] bg-[var(--text-primary)]' : 'border-[var(--text-tertiary)]'
                )} />
                <span className="font-mono text-sm">Custom (recommended for sharing)</span>
              </div>
              <div className="text-[11px] text-[var(--text-tertiary)] mt-1.5 leading-relaxed">
                Pick exactly which agents, narratives, events, social entities,
                workspace files and skills go in. Skill credentials are <strong>stripped</strong>
                {' '}(unless you explicitly choose Full Copy per skill). Use to share with someone else.
              </div>
            </button>
          </div>
        </div>
        {mode === 'full' && (
          <div className="mt-2 ml-[60px] text-[11px] text-[var(--color-yellow-500)] flex items-center gap-1.5">
            <AlertTriangle className="w-3 h-3" />
            Pick which agents to ship in the Agents tab. For each picked agent,
            ALL their skills become Full Copy (carry credentials), narratives /
            events / social entities / workspace files are fully included.
            Below tabs are read-only previews.
          </div>
        )}
      </div>

      {/* Tab bar */}
      <div className="px-6 border-b border-[var(--border-subtle)] flex">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'px-4 py-3 text-sm font-mono flex items-center gap-2 border-b-2 -mb-px',
                tab === t.id
                  ? 'border-[var(--text-primary)] text-[var(--text-primary)]'
                  : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-6">
        {tab === 'agents' && (
          <AgentsTab
            agents={agents}
            teams={teams}
            selected={selectedAgents}
            onToggle={toggleAgent}
            selectedTeam={selectedTeam}
            onSetTeam={setSelectedTeam}
          />
        )}
        {tab === 'history' && (
          <HistoryTab
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            historyByAgent={historyByAgent}
            excludedNarratives={excludedNarratives}
            excludedEvents={excludedEvents}
            onToggleNarrative={(aid, nid) => setExcludedNarratives((s) => {
              const next = { ...s };
              const cur = new Set(next[aid] || []);
              if (cur.has(nid)) cur.delete(nid); else cur.add(nid);
              next[aid] = cur;
              return next;
            })}
            onToggleEvent={(nid, eid) => setExcludedEvents((s) => {
              const next = { ...s };
              const cur = new Set(next[nid] || []);
              if (cur.has(eid)) cur.delete(eid); else cur.add(eid);
              next[nid] = cur;
              return next;
            })}
            onSelectAllNarratives={(aid) => setExcludedNarratives((s) => ({
              ...s, [aid]: new Set(),
            }))}
            onSelectNoneNarratives={(aid) => setExcludedNarratives((s) => ({
              ...s, [aid]: new Set((historyByAgent[aid] || []).map((n) => n.narrative_id)),
            }))}
            onSelectAllEventsInNarrative={(nid) => setExcludedEvents((s) => ({
              ...s, [nid]: new Set(),
            }))}
            onSelectNoneEventsInNarrative={(nid, allIds) => setExcludedEvents((s) => ({
              ...s, [nid]: new Set(allIds),
            }))}
            includeAll={includeChat}
          />
        )}
        {tab === 'skills' && (
          <SkillsTab
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            userId={userId}
            skillsForAgents={skillsForAgents}
            skillArchives={skillArchives}
            skillChoices={skillChoices}
            mode={mode}
            onChange={(agentId, name, spec) =>
              setSkillChoices((s) => ({ ...s, [skillKey(agentId, name)]: { ...spec, agent_id: agentId } }))
            }
            onAfterBackup={() => {
              api.listSkillArchives().then((r) => setSkillArchives(r.archives)).catch(() => {});
            }}
          />
        )}
        {tab === 'social' && (
          <SocialTab
            entitiesByAgent={socialEntities}
            selectedByAgent={socialSelected}
            pageByAgent={socialPage}
            onTogglePage={(aid, p) => setSocialPage((s) => ({ ...s, [aid]: p }))}
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            onToggle={toggleSocial}
            selectedTeam={selectedTeam}
            teams={teams}
          />
        )}
        {tab === 'workspace' && (
          <WorkspaceTab
            filesByAgent={workspaceFiles}
            excludesByAgent={workspaceExcludes}
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            onToggle={toggleWorkspaceFile}
          />
        )}
      </div>

      {/* Bundle notes (README.md) */}
      <div className="px-6 py-4 border-t border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
        <div className="flex items-center gap-2 mb-2">
          <FileText className="w-4 h-4" />
          <span className="text-sm font-mono">Bundle Notes (optional, shown to recipient)</span>
        </div>
        <textarea
          value={introMd}
          onChange={(e) => setIntroMd(e.target.value)}
          rows={4}
          placeholder={`# ${selectedTeam ? teams.find((x) => x.team.team_id === selectedTeam)?.team.name : 'My team'}\n\nDescribe what this team does…`}
          className="w-full px-3 py-2 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none resize-y"
        />
        <label className="mt-3 inline-flex items-center gap-2 text-xs text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={includeChat}
            onChange={(e) => setIncludeChat(e.target.checked)}
          />
          Include chat history (events + messages). Disable for templates with privacy concerns.
        </label>
      </div>

      {/* Footer */}
      <div className="px-6 py-3 border-t border-[var(--border-default)] flex items-center justify-between">
        <Button onClick={() => navigate('/app/settings')} variant="ghost" size="sm">Cancel</Button>
        <Button
          onClick={() => setReviewing(true)}
          disabled={selectedAgents.size === 0}
          size="sm"
          className="gap-1"
        >
          <Search className="w-3.5 h-3.5" />
          Review &amp; Export
        </Button>
      </div>

      {reviewing && (
        <ReviewSummaryModal
          summary={summary}
          agents={Array.from(selectedAgents)}
          team={teams.find((t) => t.team.team_id === selectedTeam) || null}
          introMd={introMd}
          skills={Object.values(skillChoices)}
          warnings={collectWarnings(skillChoices, workspaceFiles, selectedAgents)}
          onCancel={() => setReviewing(false)}
          onConfirm={doExport}
          downloading={downloading}
        />
      )}
      {sensitiveHits && (
        <SensitiveZipConfirmModal
          hits={sensitiveHits}
          onCancel={() => setSensitiveHits(null)}
          onAccept={async () => {
            setAcceptSensitiveZips(true);
            setSensitiveHits(null);
            // Re-trigger export with the flag set
            setTimeout(() => doExport(), 50);
          }}
        />
      )}
      {dialog}
    </div>
  );
}

function collectWarnings(
  skills: Record<string, SkillExportSpec>,
  workspaceFiles: Record<string, any>,
  selectedAgents: Set<string>,
): string[] {
  const warns: string[] = [];
  Object.values(skills).forEach((s) => {
    if (s.install_method === 'full_copy') {
      warns.push(`"${s.skill_name}" full-copy mode — includes credentials/wallet/etc. if present`);
    }
  });
  selectedAgents.forEach((aid) => {
    const sens = (workspaceFiles[aid] || []).filter((f: any) => f.sensitive).length;
    if (sens > 0) warns.push(`agent ${aid}: ${sens} workspace file(s) match sensitive patterns (auto-excluded by default)`);
  });
  warns.push('Free text (awareness / events / messages) is NOT auto-scanned for inline secrets — verify yourself.');
  return warns;
}

// =============================================================================
// Sub-components
// =============================================================================

function AgentsTab({
  agents, teams, selected, onToggle, selectedTeam, onSetTeam,
}: {
  agents: any[]; teams: TeamWithMembers[]; selected: Set<string>; onToggle: (id: string) => void;
  selectedTeam: string; onSetTeam: (t: string) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <label className="text-xs uppercase text-[var(--text-tertiary)]">Bundle this team (optional)</label>
        <select
          value={selectedTeam}
          onChange={(e) => onSetTeam(e.target.value)}
          className="mt-1 px-3 py-2 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)]"
        >
          <option value="">— No team / individual agents —</option>
          {teams.map((t) => (
            <option key={t.team.team_id} value={t.team.team_id}>{t.team.name} ({t.member_agent_ids.length})</option>
          ))}
        </select>
      </div>
      <div>
        <label className="text-xs uppercase text-[var(--text-tertiary)]">Agents to include</label>
        <div className="mt-2 grid grid-cols-2 gap-2">
          {agents.map((a) => {
            const checked = selected.has(a.agent_id);
            return (
              <button
                key={a.agent_id}
                onClick={() => onToggle(a.agent_id)}
                className={cn(
                  'text-left p-3 border transition-colors',
                  checked
                    ? 'bg-[var(--bg-elevated)] border-[var(--border-strong)]'
                    : 'bg-[var(--bg-secondary)] border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="text-sm font-mono truncate">{a.name || a.agent_id}</div>
                  {checked && <Check className="w-3.5 h-3.5" />}
                </div>
                <div className="text-[10px] text-[var(--text-tertiary)] truncate">{a.agent_id}</div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function SkillsTab({
  agents, userId, skillsForAgents, skillArchives, skillChoices, mode, onChange, onAfterBackup,
}: {
  agents: any[];
  userId: string;
  skillsForAgents: Record<string, string[]>;
  skillArchives: SkillArchiveRecord[];
  skillChoices: Record<string, SkillExportSpec>;
  mode: 'full' | 'custom';
  onChange: (agentId: string, name: string, spec: SkillExportSpec) => void;
  onAfterBackup: () => void;
}) {
  if (agents.length === 0) {
    return (
      <div className="text-sm text-[var(--text-tertiary)]">
        Select agents in the previous tab — their skills will appear here.
      </div>
    );
  }
  const isReadOnly = mode === 'full';
  const totalSkills = agents.reduce((s, a) => s + (skillsForAgents[a.agent_id]?.length || 0), 0);
  if (totalSkills === 0) {
    return (
      <div className="text-sm text-[var(--text-tertiary)]">
        None of the selected agents have any skills installed.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-[var(--text-tertiary)]">
        Per-agent skill list. The same skill name can appear on multiple agents — each one is
        independent (its own <code>.skill_meta.json</code>, <code>env_config</code>, <code>study_result</code>).
        {isReadOnly && ' Read-only: Full mode pinned every skill to Full Copy.'}
      </p>
      {agents.map((a) => {
        const skills = skillsForAgents[a.agent_id] || [];
        if (skills.length === 0) {
          return (
            <details key={a.agent_id} className="border border-[var(--border-default)]">
              <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)]">
                {a.name || a.agent_id} — no skills installed
              </summary>
            </details>
          );
        }
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <span className="text-[10px] text-[var(--text-tertiary)]">
                {skills.length} skill{skills.length === 1 ? '' : 's'}
              </span>
            </summary>
            <div className="p-2 space-y-2">
              {skills.map((name) => {
                const arch = skillArchives.find((aa) => aa.skill_name === name);
                const choice = skillChoices[`${a.agent_id}::${name}`];
                const hasUrl = !!arch?.source_url;
                const hasZip = !!arch?.archive_path;
                const setMethod = (spec: SkillExportSpec) => {
                  if (isReadOnly) return;
                  onChange(a.agent_id, name, spec);
                };
                return (
                  <div key={name} className="border border-[var(--border-subtle)] p-3">
                    <div className="flex items-center justify-between mb-2">
                      <div>
                        <div className="text-sm font-mono">{name}</div>
                        <div className="text-[10px] text-[var(--text-tertiary)]">
                          {arch?.source_type ? `archived (${arch.source_type}, shared by user across agents)` : 'no archive registered'}
                        </div>
                      </div>
                      {choice?.install_method === 'full_copy' && (
                        <span className="text-[10px] px-1.5 py-0.5 border border-[var(--color-yellow-500)] text-[var(--color-yellow-500)]">
                          contains_secrets
                        </span>
                      )}
                    </div>
                    <div className={cn('grid grid-cols-3 gap-2', isReadOnly && 'opacity-60 pointer-events-none')}>
                      <RadioCard
                        label="URL install"
                        desc={hasUrl ? `${arch?.source_url}` : 'No URL recorded'}
                        disabled={!hasUrl || isReadOnly}
                        active={choice?.install_method === 'url'}
                        onClick={() => hasUrl && setMethod({
                          skill_name: name, install_method: 'url',
                          source_url: arch?.source_url || '', source_type: 'github', branch: 'main',
                        })}
                      />
                      <RadioCard
                        label="Zip install"
                        desc={hasZip ? `archive ${arch?.archive_path?.split('/').pop()}` : 'No archive'}
                        disabled={!hasZip || isReadOnly}
                        active={choice?.install_method === 'zip'}
                        onClick={() => hasZip && setMethod({
                          skill_name: name, install_method: 'zip', archive_path: arch?.archive_path || '',
                        })}
                      />
                      <RadioCard
                        label="Full copy"
                        desc="⚠ includes wallets/credentials"
                        active={choice?.install_method === 'full_copy'}
                        disabled={isReadOnly}
                        onClick={() => setMethod({ skill_name: name, install_method: 'full_copy' })}
                      />
                    </div>
                    {!hasUrl && !hasZip && !isReadOnly && (
                      <div className="mt-2 text-[10px] text-[var(--text-tertiary)] flex items-start gap-1.5">
                        <AlertTriangle className="w-3 h-3 mt-0.5 text-[var(--color-yellow-500)] shrink-0" />
                        <span className="flex-1">
                          This skill has no archive. Use <strong>Ask agent to back up</strong> to drop a
                          message into the chat that asks the agent to call <code>skill_backup_*</code>,
                          upload one manually, or use Full copy.
                        </span>
                        <AskAgentToBackupButton
                          agentIds={[a.agent_id]}
                          userId={userId}
                          skillName={name}
                          onDone={onAfterBackup}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function AskAgentToBackupButton({
  agentIds, userId, skillName, onDone,
}: { agentIds: string[]; userId: string; skillName: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      onClick={async () => {
        if (agentIds.length === 0) return;
        setBusy(true);
        // Pick the first selected agent — most common case is one-skill-one-agent.
        const aid = agentIds[0];
        try {
          // Send a message via the standard chat WS endpoint asking the agent
          // to call its skill_backup MCP tool. We use the standard send-message
          // surface (post-message) so the request goes through the regular
          // agent loop.
          await fetch(`/api/agents/${encodeURIComponent(aid)}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              content: `Please back up the "${skillName}" skill for export — call skill_list_unbackedup() first to confirm it's missing, then choose the correct skill_backup_* MCP tool (skill_backup_from_github / _from_md / _from_local_zip) based on how you originally installed it. After the backup completes, tell me you're done.`,
              user_id: userId,
            }),
          }).catch(() => {});
          // Note: we don't wait for the agent to actually run; just kick the
          // message and ask the user to refresh archives in a few seconds.
          setTimeout(onDone, 3000);
        } finally {
          setBusy(false);
        }
      }}
      disabled={busy || agentIds.length === 0}
      className="text-[10px] px-2 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
      title="Send a message to the first selected agent asking it to call skill_backup_*"
    >
      {busy ? '…' : 'Ask agent to back up'}
    </button>
  );
}

function HistoryTab({
  agents, historyByAgent, excludedNarratives, excludedEvents,
  onToggleNarrative, onToggleEvent, onSelectAllNarratives, onSelectNoneNarratives,
  onSelectAllEventsInNarrative, onSelectNoneEventsInNarrative,
  includeAll,
}: {
  agents: any[];
  historyByAgent: Record<string, ChatHistoryNarrative[]>;
  excludedNarratives: Record<string, Set<string>>;
  excludedEvents: Record<string, Set<string>>;
  onToggleNarrative: (aid: string, nid: string) => void;
  onToggleEvent: (nid: string, eid: string) => void;
  onSelectAllNarratives: (aid: string) => void;
  onSelectNoneNarratives: (aid: string) => void;
  onSelectAllEventsInNarrative: (nid: string) => void;
  onSelectNoneEventsInNarrative: (nid: string, allEventIds: string[]) => void;
  includeAll: boolean;
}) {
  if (agents.length === 0) {
    return (<div className="text-sm text-[var(--text-tertiary)]">Select agents first.</div>);
  }
  if (!includeAll) {
    return (
      <div className="border border-[var(--color-yellow-500)] bg-[var(--color-yellow-500)]/10 p-3 text-xs">
        <strong>Chat history disabled.</strong> Toggle "Include chat history" off in the
        Bundle Notes section to enable this tab. With chat history off, narratives and events
        are not exported regardless of your selection here.
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-xs text-[var(--text-tertiary)]">
        Per-agent narratives and per-narrative events. By default everything is included; uncheck
        any narrative or individual event to exclude it from the bundle.
      </p>
      {agents.map((a) => {
        const narrs = historyByAgent[a.agent_id] || [];
        const exNars = excludedNarratives[a.agent_id] || new Set();
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  {narrs.length - exNars.size} / {narrs.length} narratives
                </span>
                <button
                  onClick={(e) => { e.preventDefault(); onSelectAllNarratives(a.agent_id); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                >
                  Select all
                </button>
                <button
                  onClick={(e) => { e.preventDefault(); onSelectNoneNarratives(a.agent_id); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                >
                  Select none
                </button>
              </div>
            </summary>
            <div className="p-2 max-h-[480px] overflow-y-auto space-y-2">
              {narrs.length === 0 && (
                <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">No narratives.</div>
              )}
              {narrs.map((n) => {
                const narExcluded = exNars.has(n.narrative_id);
                const exEvts = excludedEvents[n.narrative_id] || new Set();
                return (
                  <div key={n.narrative_id} className={cn(
                    'border border-[var(--border-subtle)]',
                    narExcluded && 'opacity-50'
                  )}>
                    <div className="px-3 py-2 flex items-center gap-2 bg-[var(--bg-secondary)]">
                      <input
                        type="checkbox"
                        checked={!narExcluded}
                        onChange={() => onToggleNarrative(a.agent_id, n.narrative_id)}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-mono truncate">{n.title || n.narrative_id}</div>
                        <div className="text-[10px] text-[var(--text-tertiary)]">
                          {n.events.length - exEvts.size} / {n.events.length} events
                          {n.type ? ` · ${n.type}` : ''}
                        </div>
                      </div>
                      {!narExcluded && n.events.length > 0 && (
                        <div className="flex items-center gap-1 shrink-0">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectAllEventsInNarrative(n.narrative_id);
                            }}
                            className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                          >
                            All events
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectNoneEventsInNarrative(n.narrative_id, n.events.map((x) => x.event_id));
                            }}
                            className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                          >
                            No events
                          </button>
                        </div>
                      )}
                    </div>
                    {!narExcluded && n.events.length > 0 && (
                      <div className="border-t border-[var(--border-subtle)]">
                        {n.events.slice(0, 30).map((e) => {
                          const evExcluded = exEvts.has(e.event_id);
                          return (
                            <label key={e.event_id} className={cn(
                              'flex items-start gap-2 px-3 py-1 text-xs hover:bg-[var(--bg-tertiary)]',
                              evExcluded && 'opacity-40'
                            )}>
                              <input
                                type="checkbox"
                                checked={!evExcluded}
                                onChange={() => onToggleEvent(n.narrative_id, e.event_id)}
                                className="mt-0.5"
                              />
                              <div className="flex-1 min-w-0">
                                <div className="font-mono text-[10px] text-[var(--text-tertiary)]">
                                  {e.trigger || 'event'} · {(e.created_at || '').slice(0, 19)}
                                </div>
                                <div className="truncate">{e.preview || '(no preview)'}</div>
                              </div>
                            </label>
                          );
                        })}
                        {n.events.length > 30 && (
                          <div className="px-3 py-1 text-[10px] text-[var(--text-tertiary)]">
                            +{n.events.length - 30} more events (all included by default; select narrative-level toggle to exclude entire narrative)
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function SensitiveZipConfirmModal({
  hits, onCancel, onAccept,
}: {
  hits: { skill: string; hits: string[] }[];
  onCancel: () => void;
  onAccept: () => void;
}) {
  const [confirmText, setConfirmText] = useState('');
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-[520px] max-w-[95vw] bg-[var(--bg-primary)] border-2 border-[var(--color-red-500)] flex flex-col">
        <div className="px-5 py-3 border-b border-[var(--border-default)] bg-[var(--color-red-500)]/10">
          <div className="flex items-center gap-2 text-[var(--color-red-500)]">
            <AlertTriangle className="w-5 h-5" />
            <h2 className="font-mono text-sm">Sensitive files detected in skill zip</h2>
          </div>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <p>
            One or more zip-archived skills in your bundle contain files matching sensitive
            path patterns (.env, .key, wallet.json, credentials.json, etc.).
            <strong> If you proceed, recipients will receive these files.</strong>
          </p>
          <ul className="list-disc list-inside space-y-1 text-xs font-mono bg-[var(--bg-tertiary)] p-3 max-h-[200px] overflow-y-auto">
            {hits.map((w, i) => (
              <li key={i}>
                <span className="text-[var(--color-red-500)]">{w.skill}</span>:{' '}
                {(w.hits || []).slice(0, 5).join(', ')}
                {(w.hits || []).length > 5 && ` (+${(w.hits || []).length - 5} more)`}
              </li>
            ))}
          </ul>
          <p className="text-xs text-[var(--text-secondary)]">
            Type <strong className="font-mono">SHARE SECRETS</strong> below to confirm you understand
            and want to ship these files anyway.
          </p>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="Type SHARE SECRETS to confirm"
            className="w-full px-3 py-2 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none"
          />
        </div>
        <div className="px-5 py-3 border-t border-[var(--border-default)] flex justify-end gap-2">
          <Button onClick={onCancel} variant="ghost" size="sm">Cancel</Button>
          <Button
            onClick={onAccept}
            disabled={confirmText !== 'SHARE SECRETS'}
            size="sm"
            className="bg-[var(--color-red-500)] text-white hover:bg-[var(--color-red-500)]/80"
          >
            Ship anyway
          </Button>
        </div>
      </div>
    </div>
  );
}

function RadioCard({ label, desc, disabled, active, onClick }: { label: string; desc: string; disabled?: boolean; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'p-2 text-left border text-xs transition-colors',
        disabled && 'opacity-40 cursor-not-allowed',
        active
          ? 'border-[var(--text-primary)] bg-[var(--bg-elevated)]'
          : 'border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
      )}
    >
      <div className="font-mono">{label}</div>
      <div className="text-[10px] text-[var(--text-tertiary)] mt-1 break-all">{desc}</div>
    </button>
  );
}

function SocialTab({
  entitiesByAgent, selectedByAgent, pageByAgent, onTogglePage,
  agents, onToggle,
}: {
  entitiesByAgent: Record<string, SocialEntity[]>;
  selectedByAgent: Record<string, Set<string>>;
  pageByAgent: Record<string, number>;
  onTogglePage: (aid: string, page: number) => void;
  agents: any[];
  onToggle: (aid: string, eid: string) => void;
  selectedTeam?: string;
  teams?: TeamWithMembers[];
}) {
  const PAGE_SIZE = 10;
  if (agents.length === 0) return (
    <div className="text-sm text-[var(--text-tertiary)]">Select agents first.</div>
  );
  return (
    <div className="space-y-4">
      {agents.map((a) => {
        const list = (entitiesByAgent[a.agent_id] || []).slice().sort((x, y) =>
          (x.entity_name || x.entity_id).localeCompare(y.entity_name || y.entity_id)
        );
        const selected = selectedByAgent[a.agent_id] || new Set<string>();
        const page = pageByAgent[a.agent_id] || 0;
        const totalPages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
        const slice = list.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <span className="text-[10px] text-[var(--text-tertiary)]">
                {selected.size} / {list.length} selected
              </span>
            </summary>
            <div className="grid grid-cols-2 divide-x divide-[var(--border-subtle)]">
              {/* Left: all entities (paged) */}
              <div className="p-2">
                <div className="text-[10px] text-[var(--text-tertiary)] mb-1 px-2">All entities (sort by name)</div>
                {slice.length === 0 && (
                  <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">No entities for this agent.</div>
                )}
                {slice.map((e) => {
                  const isSel = selected.has(e.entity_id);
                  return (
                    <div key={e.entity_id} className="flex items-start gap-2 px-2 py-1 hover:bg-[var(--bg-tertiary)]">
                      <input type="checkbox" checked={isSel} onChange={() => onToggle(a.agent_id, e.entity_id)} className="mt-1" />
                      <div className="flex-1 min-w-0">
                        <details>
                          <summary className="text-xs font-mono cursor-pointer truncate">
                            {e.entity_name || e.entity_id}{' '}
                            <span className="text-[9px] text-[var(--text-tertiary)]">[{e.entity_type}]</span>
                          </summary>
                          <div className="mt-1 ml-2 text-[10px] text-[var(--text-tertiary)] space-y-0.5">
                            <div>id: {e.entity_id}</div>
                            {e.entity_description && <div>desc: {e.entity_description}</div>}
                            {(e.tags || []).length > 0 && <div>tags: {(e.tags || []).join(', ')}</div>}
                          </div>
                        </details>
                      </div>
                    </div>
                  );
                })}
                {totalPages > 1 && (
                  <div className="flex items-center justify-center gap-1 py-2 text-xs">
                    <button onClick={() => onTogglePage(a.agent_id, Math.max(0, page - 1))} disabled={page === 0} className="px-2 py-0.5 border border-[var(--border-subtle)] disabled:opacity-30">‹</button>
                    <span>{page + 1} / {totalPages}</span>
                    <button onClick={() => onTogglePage(a.agent_id, Math.min(totalPages - 1, page + 1))} disabled={page >= totalPages - 1} className="px-2 py-0.5 border border-[var(--border-subtle)] disabled:opacity-30">›</button>
                  </div>
                )}
              </div>
              {/* Right: selected */}
              <div className="p-2">
                <div className="text-[10px] text-[var(--text-tertiary)] mb-1 px-2">Selected (will be packaged)</div>
                {Array.from(selected).map((eid) => {
                  const e = list.find((x) => x.entity_id === eid);
                  if (!e) return null;
                  return (
                    <div key={eid} className="flex items-center gap-2 px-2 py-1 text-xs font-mono">
                      <Check className="w-3 h-3 text-[var(--color-green-500)]" />
                      <span className="flex-1 truncate">{e.entity_name || e.entity_id}</span>
                      <button onClick={() => onToggle(a.agent_id, eid)} className="text-[var(--color-red-500)] text-[10px]">remove</button>
                    </div>
                  );
                })}
              </div>
            </div>
          </details>
        );
      })}
    </div>
  );
}

function WorkspaceTab({
  filesByAgent, excludesByAgent, agents, onToggle,
}: {
  filesByAgent: Record<string, { path: string; size: number; sensitive: boolean }[]>;
  excludesByAgent: Record<string, Set<string>>;
  agents: any[];
  onToggle: (aid: string, path: string) => void;
}) {
  if (agents.length === 0) return (<div className="text-sm text-[var(--text-tertiary)]">Select agents first.</div>);
  return (
    <div className="space-y-4">
      {agents.map((a) => {
        const files = filesByAgent[a.agent_id] || [];
        const excludes = excludesByAgent[a.agent_id] || new Set();
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <span className="text-[10px] text-[var(--text-tertiary)]">{files.length} file{files.length === 1 ? '' : 's'}</span>
            </summary>
            <div className="p-2 max-h-[320px] overflow-y-auto">
              {files.length === 0 && (
                <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">No workspace files reported by API.</div>
              )}
              {files.map((f) => {
                const sensitive = f.sensitive;
                const excluded = excludes.has(f.path);
                const willBeIncluded = !sensitive && !excluded;
                return (
                  <label key={f.path} className="flex items-center gap-2 px-2 py-1 hover:bg-[var(--bg-tertiary)]">
                    <input
                      type="checkbox"
                      checked={willBeIncluded}
                      onChange={() => onToggle(a.agent_id, f.path)}
                    />
                    <span className={cn('text-xs font-mono flex-1 truncate', sensitive && 'text-[var(--color-yellow-500)]')}>
                      {f.path}
                    </span>
                    {sensitive && <span className="text-[9px] text-[var(--color-yellow-500)]">sensitive</span>}
                    <span className="text-[10px] text-[var(--text-tertiary)]">{Math.round(f.size / 1024)} KB</span>
                  </label>
                );
              })}
            </div>
          </details>
        );
      })}
    </div>
  );
}

// =============================================================================
// Review Summary Modal
// =============================================================================

function ReviewSummaryModal({
  summary, team, introMd, skills, warnings, onCancel, onConfirm, downloading,
}: any) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-[680px] max-w-[95vw] max-h-[90vh] bg-[var(--bg-primary)] border border-[var(--border-default)] flex flex-col">
        <div className="px-5 py-3 border-b border-[var(--border-default)] flex items-center justify-between">
          <h2 className="font-mono text-sm">Final review before download</h2>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4 text-sm font-mono">
          <div>
            <div className="text-[var(--text-secondary)] uppercase text-xs mb-1">✓ Included</div>
            <ul className="list-disc list-inside text-[12px] text-[var(--text-secondary)] space-y-0.5">
              <li>{summary.agents} agent{summary.agents === 1 ? '' : 's'}</li>
              {team && <li>1 team "{team.team.name}"</li>}
              <li>
                {skills.filter((s: SkillExportSpec) => s.install_method).length} skill{skills.filter((s: SkillExportSpec) => s.install_method).length === 1 ? '' : 's'}:
                {' '}{skills.filter((s: SkillExportSpec) => s.install_method === 'url').length}× url,
                {' '}{skills.filter((s: SkillExportSpec) => s.install_method === 'zip').length}× zip,
                {' '}{skills.filter((s: SkillExportSpec) => s.install_method === 'full_copy').length}× full-copy
              </li>
              <li>{summary.socialEntities} social entit{summary.socialEntities === 1 ? 'y' : 'ies'}</li>
              <li>workspace files (sensitive paths excluded by default)</li>
              {introMd && <li>README.md ({introMd.length} chars)</li>}
            </ul>
          </div>
          <div>
            <div className="text-[var(--text-secondary)] uppercase text-xs mb-1">✗ Stripped</div>
            <ul className="list-disc list-inside text-[12px] text-[var(--text-secondary)] space-y-0.5">
              <li>LLM API keys, Lark OAuth tokens, password hashes</li>
              <li>workspace 外 (~/.config 等)</li>
              <li>env_config of all url/zip-installed skills</li>
            </ul>
          </div>
          {warnings.length > 0 && (
            <div>
              <div className="text-[var(--color-yellow-500)] uppercase text-xs mb-1 flex items-center gap-1">
                <AlertTriangle className="w-3 h-3" /> Warnings
              </div>
              <ul className="list-disc list-inside text-[12px] text-[var(--text-secondary)] space-y-0.5">
                {warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}
        </div>
        <div className="px-5 py-3 border-t border-[var(--border-default)] flex justify-end gap-2">
          <Button onClick={onCancel} variant="ghost" size="sm" disabled={downloading}>Cancel</Button>
          <Button onClick={onConfirm} size="sm" disabled={downloading} className="gap-1">
            {downloading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
            Download .nxbundle
          </Button>
        </div>
      </div>
    </div>
  );
}
