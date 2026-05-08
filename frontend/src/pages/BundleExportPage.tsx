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
import { useNavigate } from 'react-router-dom';
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
  { id: 'skills', label: 'Skills', icon: Wrench },
  { id: 'social', label: 'Social Network', icon: Hexagon },
  { id: 'workspace', label: 'Workspace files', icon: ListTree },
];

type TabId = 'agents' | 'skills' | 'social' | 'workspace';

interface SocialEntity {
  entity_id: string;
  entity_type: string;
  entity_name?: string | null;
  entity_description?: string | null;
  tags?: string[] | null;
  instance_id: string;
}

export default function BundleExportPage() {
  const navigate = useNavigate();
  const { agents, userId } = useConfigStore();
  const { teams, refresh: refreshTeams } = useTeamsStore();
  const { alert, dialog } = useConfirm();

  const [tab, setTab] = useState<TabId>('agents');
  const [selectedAgents, setSelectedAgents] = useState<Set<string>>(new Set());
  const [selectedTeam, setSelectedTeam] = useState<string>('');
  const [introMd, setIntroMd] = useState('');
  const [includeChat, setIncludeChat] = useState(true);

  // Skills state
  const [skillsForAgents, setSkillsForAgents] = useState<Record<string, string[]>>({});
  const [skillArchives, setSkillArchives] = useState<SkillArchiveRecord[]>([]);
  const [skillChoices, setSkillChoices] = useState<Record<string, SkillExportSpec>>({});

  // Social state
  const [socialEntities, setSocialEntities] = useState<Record<string, SocialEntity[]>>({});
  const [socialSelected, setSocialSelected] = useState<Record<string, Set<string>>>({});
  const [socialPage, setSocialPage] = useState<Record<string, number>>({});

  // Workspace state
  const [workspaceFiles, setWorkspaceFiles] = useState<Record<string, { path: string; size: number; sensitive: boolean }[]>>({});
  const [workspaceExcludes, setWorkspaceExcludes] = useState<Record<string, Set<string>>>({});

  const [reviewing, setReviewing] = useState(false);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => { refreshTeams(); }, [refreshTeams]);
  useEffect(() => {
    api.listSkillArchives().then((r) => setSkillArchives(r.archives)).catch(() => {});
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
            // Backend response shape: { entities: [...] } or similar
            const entities: SocialEntity[] = (r.entities || r.network || []).map((e: any) => ({
              entity_id: e.entity_id,
              entity_type: e.entity_type,
              entity_name: e.entity_name,
              entity_description: e.entity_description,
              tags: e.tags || [],
              instance_id: e.instance_id,
            }));
            setSocialEntities((s) => ({ ...s, [aid]: entities }));
          }).catch(() => {});
      }
      if (!workspaceFiles[aid]) {
        api.listFiles(aid, userId)
          .then((r: any) => {
            const files = (r.files || []).map((f: any) => ({
              path: f.path || f.name,
              size: f.size || 0,
              sensitive: isSensitive(f.path || f.name),
            }));
            setWorkspaceFiles((s) => ({ ...s, [aid]: files }));
          }).catch(() => {});
      }
    });
  }, [selectedAgents, userId]);

  // Default skill choice based on archive availability
  useEffect(() => {
    const newChoices: Record<string, SkillExportSpec> = {};
    Object.values(skillsForAgents).flat().forEach((skill) => {
      if (skillChoices[skill]) {
        newChoices[skill] = skillChoices[skill];
        return;
      }
      const arch = skillArchives.find((a) => a.skill_name === skill);
      if (arch?.source_url) {
        newChoices[skill] = {
          skill_name: skill,
          install_method: 'url',
          source_url: arch.source_url,
          source_type: 'github',
          branch: 'main',
        };
      } else if (arch?.archive_path) {
        newChoices[skill] = {
          skill_name: skill,
          install_method: 'zip',
          archive_path: arch.archive_path,
        };
      } else {
        // Default to "skip" semantically by leaving install_method blank
        newChoices[skill] = {
          skill_name: skill,
          install_method: 'full_copy',
        };
      }
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
    setSelectedAgents((prev) => {
      const next = new Set(prev);
      if (next.has(aid)) next.delete(aid);
      else next.add(aid);
      return next;
    });
  }

  function toggleSocial(aid: string, eid: string) {
    setSocialSelected((prev) => {
      const next = { ...prev };
      const set = new Set(next[aid] || []);
      if (set.has(eid)) set.delete(eid); else set.add(eid);
      next[aid] = set;
      return next;
    });
  }

  function toggleWorkspaceFile(aid: string, path: string) {
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
      const payload: BundleExportRequest = {
        agent_ids: Array.from(selectedAgents),
        team_id: selectedTeam || null,
        team_intro_md: introMd || null,
        skills,
        social_entity_selection: social,
        workspace_excludes: excludes,
        include_chat_history: includeChat,
      };
      const { blob, filename, warningsCount } = await api.exportBundle(payload);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
      await alert({
        title: 'Bundle created',
        message: `${filename} downloaded.${warningsCount ? ` ${warningsCount} warning${warningsCount === 1 ? '' : 's'} (see manifest.json).` : ''}`,
      });
      navigate('/app/settings');
    } catch (e: any) {
      console.error(e);
      await alert({ title: 'Export failed', message: e?.message || String(e), danger: true });
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
        {tab === 'skills' && (
          <SkillsTab
            skillsForAgents={skillsForAgents}
            skillArchives={skillArchives}
            skillChoices={skillChoices}
            onChange={(name, spec) => setSkillChoices((s) => ({ ...s, [name]: spec }))}
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
  skillsForAgents, skillArchives, skillChoices, onChange,
}: {
  skillsForAgents: Record<string, string[]>;
  skillArchives: SkillArchiveRecord[];
  skillChoices: Record<string, SkillExportSpec>;
  onChange: (name: string, spec: SkillExportSpec) => void;
}) {
  const allSkills = Array.from(new Set(Object.values(skillsForAgents).flat()));
  if (allSkills.length === 0) {
    return (
      <div className="text-sm text-[var(--text-tertiary)]">
        Select agents in the previous tab — their skills will appear here.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-[var(--text-tertiary)]">
        For each skill, pick how it should be reproducible by the bundle recipient. The default
        is the safest available option.
      </p>
      {allSkills.map((name) => {
        const arch = skillArchives.find((a) => a.skill_name === name);
        const choice = skillChoices[name];
        const hasUrl = !!arch?.source_url;
        const hasZip = !!arch?.archive_path;
        return (
          <div key={name} className="border border-[var(--border-default)] p-3">
            <div className="flex items-center justify-between mb-2">
              <div>
                <div className="text-sm font-mono">{name}</div>
                <div className="text-[10px] text-[var(--text-tertiary)]">
                  {arch?.source_type ? `archived (${arch.source_type})` : 'no archive registered'}
                </div>
              </div>
              {choice?.install_method === 'full_copy' && (
                <span className="text-[10px] px-1.5 py-0.5 border border-[var(--color-yellow-500)] text-[var(--color-yellow-500)]">
                  contains_secrets
                </span>
              )}
            </div>
            <div className="grid grid-cols-3 gap-2">
              <RadioCard
                label="URL install"
                desc={hasUrl ? `${arch?.source_url} (${arch?.source_type})` : 'No URL recorded'}
                disabled={!hasUrl}
                active={choice?.install_method === 'url'}
                onClick={() => hasUrl && onChange(name, {
                  skill_name: name, install_method: 'url',
                  source_url: arch?.source_url || '', source_type: 'github', branch: 'main',
                })}
              />
              <RadioCard
                label="Zip install"
                desc={hasZip ? `archive ${arch?.archive_path?.split('/').pop()}` : 'No archive'}
                disabled={!hasZip}
                active={choice?.install_method === 'zip'}
                onClick={() => hasZip && onChange(name, {
                  skill_name: name, install_method: 'zip', archive_path: arch?.archive_path || '',
                })}
              />
              <RadioCard
                label="Full copy"
                desc="⚠ includes wallets/credentials"
                active={choice?.install_method === 'full_copy'}
                onClick={() => onChange(name, { skill_name: name, install_method: 'full_copy' })}
              />
            </div>
            {!hasUrl && !hasZip && (
              <div className="mt-2 text-[10px] text-[var(--text-tertiary)] flex items-start gap-1">
                <AlertTriangle className="w-3 h-3 mt-0.5" />
                <span>This skill has no archive. Either ask your agent to call <code>skill_backup_*</code>, upload one in Settings → Skill archives, or use Full copy.</span>
              </div>
            )}
          </div>
        );
      })}
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
