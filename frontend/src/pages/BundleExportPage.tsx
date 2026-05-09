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

interface ChatHistoryJob {
  job_id: string;
  title?: string;
  description?: string;
  status?: string;
  job_type?: string;
}

interface ChatHistoryNarrative {
  narrative_id: string;
  // human-readable label — derived from API's `name` (preferred) or first event preview
  title: string;
  description?: string;
  current_summary?: string;
  type?: string;
  instances_count?: number;
  events: ChatHistoryEvent[];
  jobs: ChatHistoryJob[];      // P7: jobs grouped by parent narrative
  created_at?: string;
}

export default function BundleExportPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { agents, userId } = useConfigStore();
  const { teams, refresh: refreshTeams } = useTeamsStore();
  const { alert, confirm: confirmDialog, dialog } = useConfirm();

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
  // Right-pane (selected entities) pagination — independent from left.
  const [socialSelectedPage, setSocialSelectedPage] = useState<Record<string, number>>({});

  // Workspace state
  const [workspaceFiles, setWorkspaceFiles] = useState<Record<string, { path: string; size: number; sensitive: boolean }[]>>({});
  const [workspaceExcludes, setWorkspaceExcludes] = useState<Record<string, Set<string>>>({});

  // B2: chat history selection state — narrative-level allowlist (per agent)
  // and event-level allowlist (per narrative). Default = all included.
  const [historyByAgent, setHistoryByAgent] = useState<Record<string, ChatHistoryNarrative[]>>({});
  const [excludedNarratives, setExcludedNarratives] = useState<Record<string, Set<string>>>({});
  const [excludedEvents, setExcludedEvents] = useState<Record<string, Set<string>>>({});
  // P7: per-narrative job exclusion (default = include). When the parent
  // narrative is excluded, its jobs are auto-dropped on the backend (P4)
  // regardless of this set.
  const [excludedJobs, setExcludedJobs] = useState<Record<string, Set<string>>>({});

  // P9: bundle filename — defaults to <team_name>-YYYYMMDD.nxbundle when a
  // team is selected, else bundle-<YYYYMMDD>.nxbundle. Editable.
  const [filename, setFilename] = useState<string>('');

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

  // P9: derive default filename from selected team. User can still type over
  // it. We only auto-fill when the field is empty OR matches a previous
  // auto-fill (i.e. the user hasn't typed anything custom).
  useEffect(() => {
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const team = teams.find((t) => t.team.team_id === selectedTeam);
    const safeName = team
      ? team.team.name.replace(/[^a-zA-Z0-9_一-龥-]/g, '_').replace(/__+/g, '_')
      : 'bundle';
    const def = `${safeName}-${stamp}.nxbundle`;
    setFilename((cur) => {
      // Only overwrite if empty, or if it still looks like a previous default
      // (matches `<anything>-YYYYMMDD.nxbundle`).
      if (!cur) return def;
      if (/-\d{8}\.nxbundle$/.test(cur)) return def;
      return cur;
    });
  }, [selectedTeam, teams]);

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
      // Chat history (B2/P1) — actual API shape:
      //   { narratives: [{narrative_id, name, description, current_summary,
      //                   instances, ...}], events: [{event_id, narrative_id,
      //                   final_output, ...}] }   <- events are FLAT, not nested!
      // Plus we fetch jobs (/api/jobs?agent_id=...) and group by narrative_id (P7).
      if (!historyByAgent[aid]) {
        Promise.all([
          api.getChatHistory(aid, userId).catch(() => null),
          api.getJobs(aid, userId).catch(() => null),
        ]).then(([chRaw, jobsRaw]: any[]) => {
          const ch: any = chRaw || {};
          const jobs: any = jobsRaw || {};
          // Bucket events by narrative_id
          const eventsByNar: Record<string, ChatHistoryEvent[]> = {};
          for (const e of (ch.events || [])) {
            const nid = e.narrative_id;
            if (!nid) continue;
            (eventsByNar[nid] ??= []).push({
              event_id: e.event_id,
              trigger: e.trigger,
              created_at: e.created_at,
              preview: (e.final_output || '').slice(0, 100),
            });
          }
          // Bucket jobs by narrative_id (jobs without parent narrative go in
          // a synthetic "(no narrative)" bucket the user can still skip)
          const jobsByNar: Record<string, ChatHistoryJob[]> = {};
          for (const j of (jobs.jobs || [])) {
            const nid = j.narrative_id || '__orphan__';
            (jobsByNar[nid] ??= []).push({
              job_id: j.job_id,
              title: j.title,
              description: j.description,
              status: j.status,
              job_type: j.job_type,
            });
          }
          const narrs: ChatHistoryNarrative[] = (ch.narratives || []).map((n: any) => ({
            narrative_id: n.narrative_id,
            title: n.name || n.narrative_id,                 // human-readable
            description: n.description,
            current_summary: n.current_summary,
            type: n.type,
            instances_count: (n.instances || []).length,
            events: eventsByNar[n.narrative_id] || [],
            jobs: jobsByNar[n.narrative_id] || [],
            created_at: n.created_at,
          }));
          // Surface orphan jobs (no parent narrative) under a synthetic row
          if (jobsByNar['__orphan__']) {
            narrs.push({
              narrative_id: '__orphan_jobs__',
              title: '(jobs without a parent narrative)',
              events: [],
              jobs: jobsByNar['__orphan__'],
            });
          }
          setHistoryByAgent((s) => ({ ...s, [aid]: narrs }));
        });
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

  // P8: default social entity selection — only entries that reference
  // agents IN this bundle's closure. Strict matching:
  //   * entity_type MUST be 'agent' (skip user/organization/etc — user can
  //     manually opt them in)
  //   * AND one of:
  //       - entity_id ∈ closure (or contains a closure agent_id substring)
  //       - entity_name == any closure agent's name (case-insensitive
  //         exact-or-token match — not naive substring like "Loki" matching
  //         "lokitrickster")
  //       - any closure agent's name OR agent_id appears as a TOKEN in
  //         tags / description / aliases / contact_info / extra_data
  // We avoid pure substring search because real entities like "Loki" /
  // "Iris" are 3-4 letters and would over-match (e.g. an entity named
  // "iris_8ec0" would match "Iris", a teammate name, even though they're
  // different agents). Word-boundary regex avoids that.
  useEffect(() => {
    // Full mode = "ship everything"; the dedicated Full-mode effect below
    // handles social-entity selection (selects every entity), so this
    // strict-matching default-selector should NOT run in Full mode (it
    // would otherwise overwrite the all-selected state on every render).
    if (mode === 'full') return;
    // Build the closure-agent identity set: each entry has both name and id
    // we should match on. Names can repeat (different agent same name) so we
    // dedupe per closure agent.
    const closureAgents: { id: string; nameLower: string; idLower: string }[] = [];
    Array.from(selectedAgents).forEach((aid) => {
      const a = agents.find((x) => x.agent_id === aid);
      const nm = (a?.name || '').trim();
      if (nm) {
        closureAgents.push({ id: aid, nameLower: nm.toLowerCase(), idLower: aid.toLowerCase() });
      } else {
        closureAgents.push({ id: aid, nameLower: '', idLower: aid.toLowerCase() });
      }
    });

    const wordBoundary = (haystack: string, needle: string): boolean => {
      if (!needle) return false;
      const escaped = needle.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&');
      // Treat any non-alnum-Chinese as boundary
      const re = new RegExp(`(^|[^\\w\\u4e00-\\u9fa5])${escaped}([^\\w\\u4e00-\\u9fa5]|$)`, 'i');
      return re.test(haystack);
    };

    const matchesClosure = (e: SocialEntity): boolean => {
      // Only agent-typed entities are eligible for default selection (P8).
      if (e.entity_type !== 'agent') return false;
      const eid = (e.entity_id || '').toLowerCase();
      const en = (e.entity_name || '').toLowerCase();
      // Aggregate every text field into one haystack we can scan
      const haystackParts: string[] = [
        eid, en,
        (e.entity_description || ''),
        ...(e.tags || []),
      ];
      const hay = haystackParts.filter(Boolean).join('\n').toLowerCase();
      for (const ca of closureAgents) {
        // Exact id match
        if (eid === ca.idLower) return true;
        // Name match (case-insensitive exact, or token-bounded inside a longer string)
        if (ca.nameLower && (en === ca.nameLower || wordBoundary(en, ca.nameLower))) return true;
        // Any closure agent's full id or name appears as token in haystack
        if (ca.nameLower && wordBoundary(hay, ca.nameLower)) return true;
        if (wordBoundary(hay, ca.idLower)) return true;
      }
      return false;
    };

    const next: Record<string, Set<string>> = {};
    Object.entries(socialEntities).forEach(([aid, list]) => {
      const set = new Set<string>();
      list.forEach((e) => {
        if (matchesClosure(e)) set.add(e.entity_id);
      });
      next[aid] = set;
    });
    setSocialSelected(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, JSON.stringify(socialEntities), JSON.stringify(Array.from(selectedAgents)), JSON.stringify(agents.map(a => a.agent_id + ':' + a.name))]);

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

  async function toggleWorkspaceFile(aid: string, path: string) {
    if (mode === 'full') return;
    const file = (workspaceFiles[aid] || []).find((f) => f.path === path);
    const isSensitive = !!file?.sensitive;
    const inSet = (workspaceExcludes[aid] || new Set()).has(path);

    // workspaceExcludes set has DUAL meaning per file kind:
    //   - non-sensitive file: presence ↔ user-excluded (default include)
    //   - sensitive file:     presence ↔ user override-include (default exclude)
    //
    // So clicking the checkbox does:
    //   - non-sensitive, NOT in set → add to set       : default-include → exclude   (no prompt)
    //   - non-sensitive, in set     → remove from set  : excluded → default-include  (no prompt)
    //   - sensitive,     NOT in set → add to set       : default-skip → user opts-in (CONFIRM)
    //   - sensitive,     in set     → remove from set  : user-override-include → back to default skip (no prompt)
    //
    // Confirm modal only fires when the click is going to put a sensitive
    // file's bytes INTO the bundle for the first time.
    const isAddingToSet = !inSet;
    const isOptingInSensitive = isSensitive && isAddingToSet;

    if (isOptingInSensitive) {
      const ok = await confirmDialog({
        title: 'Include sensitive file?',
        message: (
          <>
            <p><code>{path}</code> matches a sensitive-pattern (e.g. <code>.env</code>, <code>*.key</code>, credentials, wallet).</p>
            <p>Including it will ship the file's contents inside the bundle. Anyone you share the bundle with will receive these bytes.</p>
            <p>Confirm only if you've verified this file does not contain secrets you want to keep private.</p>
          </>
        ),
        confirmText: 'Include anyway',
        danger: true,
      });
      if (!ok) return;
    }
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
    // 2. Force every (agent, skill) pair to full_copy mode.
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
    // 3. Select EVERY social entity for every agent (no type filter — Full
    //    ships agent / user / organization / etc.).
    setSocialSelected((cur) => {
      const out = { ...cur };
      Object.entries(socialEntities).forEach(([aid, list]) => {
        out[aid] = new Set(list.map((e) => e.entity_id));
      });
      return out;
    });
    // 4. History: include all narratives + events + jobs.
    setExcludedNarratives({});
    setExcludedEvents({});
    setExcludedJobs({});
    setIncludeChat(true);
    // 5. Workspace excludes: dual-meaning set (see toggleWorkspaceFile).
    //    Non-sensitive default = include (set membership = exclude). Full
    //    keeps non-sensitive set entries empty.
    //    Sensitive default = exclude (set membership = override-include).
    //    For Full snapshot we want sensitive files INCLUDED, so we PRELOAD
    //    every sensitive file path into the override-include set per agent.
    setWorkspaceExcludes(() => {
      const out: Record<string, Set<string>> = {};
      Object.entries(workspaceFiles).forEach(([aid, files]) => {
        const sensitivePaths = files.filter((f) => f.sensitive).map((f) => f.path);
        if (sensitivePaths.length > 0) out[aid] = new Set(sensitivePaths);
      });
      return out;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, JSON.stringify(skillsForAgents), JSON.stringify(socialEntities), JSON.stringify(workspaceFiles)]);

  async function doExport() {
    setDownloading(true);
    try {
      const skills: SkillExportSpec[] = Object.values(skillChoices).filter((s) => !!s.install_method);
      const social: Record<string, string[]> = {};
      Object.entries(socialSelected).forEach(([aid, set]) => {
        social[aid] = Array.from(set);
      });
      // P6: workspaceExcludes set has dual meaning per file:
      //   - non-sensitive file in set → user wants to EXCLUDE it
      //   - sensitive file in set → user opted-IN (override the default-skip)
      // Final exclude list emitted to backend = (non-sensitive in set) ∪
      // (sensitive NOT in set). This way sensitive files default-skip but the
      // user can opt-in via the confirm modal.
      const excludes: Record<string, string[]> = {};
      Array.from(selectedAgents).forEach((aid) => {
        const allFiles = workspaceFiles[aid] || [];
        const userSet = workspaceExcludes[aid] || new Set();
        const out = new Set<string>();
        for (const f of allFiles) {
          if (f.sensitive) {
            // default: exclude. user-override-set membership flips to include.
            if (!userSet.has(f.path)) out.add(f.path);
          } else {
            // default: include. set membership = exclude.
            if (userSet.has(f.path)) out.add(f.path);
          }
        }
        if (out.size > 0) excludes[aid] = Array.from(out);
      });
      // B2: derive narrative + event allowlists from "exclusion" sets
      const narrativeSel: Record<string, string[]> = {};
      const eventSel: Record<string, string[]> = {};
      Array.from(selectedAgents).forEach((aid) => {
        const allNarrs = historyByAgent[aid] || [];
        // Filter out the synthetic orphan-jobs placeholder narrative —
        // it's a UI-only construct, not a real narrative_id, and
        // shouldn't enter the backend allowlist.
        const realNarrs = allNarrs.filter((n) => n.narrative_id !== '__orphan_jobs__');
        const exNars = excludedNarratives[aid] || new Set();
        // Only emit a selection if user actually de-selected something;
        // otherwise leave undefined to fall back to "include all" semantics.
        if (exNars.size > 0) {
          narrativeSel[aid] = realNarrs
            .filter((n) => !exNars.has(n.narrative_id))
            .map((n) => n.narrative_id);
        }
        // Per-narrative event filtering (skip orphan placeholder which has no events)
        realNarrs.forEach((n) => {
          const exEvts = excludedEvents[n.narrative_id];
          if (exEvts && exEvts.size > 0) {
            eventSel[n.narrative_id] = n.events
              .filter((e) => !exEvts.has(e.event_id))
              .map((e) => e.event_id);
          }
        });
      });
      // P7: per-agent job allowlist derived from `excludedJobs`. We only
      // emit a selection if the user actually de-selected something (else
      // backend's "include all" default applies).
      const jobSel: Record<string, string[]> = {};
      Array.from(selectedAgents).forEach((aid) => {
        const allNarrs = historyByAgent[aid] || [];
        const exNars = excludedNarratives[aid] || new Set();
        const ids: string[] = [];
        let hasExclusion = false;
        allNarrs.forEach((n) => {
          if (exNars.has(n.narrative_id)) return;  // narrative excluded → builder drops jobs anyway
          const exJobs = excludedJobs[n.narrative_id];
          if (exJobs && exJobs.size > 0) {
            hasExclusion = true;
            n.jobs.forEach((j) => { if (!exJobs.has(j.job_id)) ids.push(j.job_id); });
          } else {
            n.jobs.forEach((j) => ids.push(j.job_id));
          }
        });
        if (hasExclusion) jobSel[aid] = ids;
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
        job_selection: Object.keys(jobSel).length ? jobSel : null,
      };
      const { blob, filename: serverFilename, warningsCount, externalEdgesDropped } = await api.exportBundle(payload);
      // (filename sanitization happens just below; reuse `finalName`)
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      // Warnings are real concerns; "external edges dropped" is informational
      // (expected closure behavior) and shown separately so the user doesn't
      // panic over hundreds of routine edge-drops.
      // Sanitize filename: strip path-traversal chars, ensure .nxbundle suffix.
      let chosen = (filename && filename.trim()) || serverFilename;
      // Disallow `/` and `\` to avoid the browser writing into a subdirectory.
      chosen = chosen.replace(/[\/\\]/g, '_');
      if (!chosen.endsWith('.nxbundle') && !chosen.endsWith('.zip')) {
        chosen = `${chosen}.nxbundle`;
      }
      const finalName = chosen;
      a.download = finalName;
      a.click();
      URL.revokeObjectURL(url);
      const parts = [`${finalName} downloaded.`];
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
            excludedJobs={excludedJobs}
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
            onToggleJob={(nid, jid) => setExcludedJobs((s) => {
              const next = { ...s };
              const cur = new Set(next[nid] || []);
              if (cur.has(jid)) cur.delete(jid); else cur.add(jid);
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
            onSelectAllJobsInNarrative={(nid) => setExcludedJobs((s) => ({
              ...s, [nid]: new Set(),
            }))}
            onSelectNoneJobsInNarrative={(nid, allIds) => setExcludedJobs((s) => ({
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
            selectedPageByAgent={socialSelectedPage}
            onToggleSelectedPage={(aid, p) => setSocialSelectedPage((s) => ({ ...s, [aid]: p }))}
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            onToggle={toggleSocial}
            onBulkSet={(aid, ids) => setSocialSelected((s) => ({ ...s, [aid]: new Set(ids) }))}
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
            onBulkSet={(aid, scope) => {
              const files = workspaceFiles[aid] || [];
              setWorkspaceExcludes((s) => {
                const next = { ...s };
                if (scope === 'all') {
                  // all included: sensitive get override-include (in set);
                  // non-sensitive default-include (NOT in set)
                  next[aid] = new Set(files.filter((f) => f.sensitive).map((f) => f.path));
                } else if (scope === 'non-sensitive') {
                  // back to defaults: all non-sensitive included, all sensitive skipped
                  next[aid] = new Set();
                } else {
                  // exclude-all: every non-sensitive in set; no sensitive override
                  next[aid] = new Set(files.filter((f) => !f.sensitive).map((f) => f.path));
                }
                return next;
              });
            }}
          />
        )}
      </div>

      {/* Bundle notes (README.md) + filename (P9) */}
      <div className="px-6 py-4 border-t border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
        {/* Filename input */}
        <div className="flex items-center gap-2 mb-3">
          <span className="text-xs font-mono uppercase tracking-widest text-[var(--text-tertiary)] shrink-0">
            File name
          </span>
          <input
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            placeholder="bundle.nxbundle"
            className="flex-1 px-3 py-1.5 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none"
          />
        </div>
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
          filename={filename}
          mode={mode}
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
  const allLoaded = agents.every((a) => a.agent_id in skillsForAgents);
  const totalSkills = agents.reduce((s, a) => s + (skillsForAgents[a.agent_id]?.length || 0), 0);
  if (!allLoaded && totalSkills === 0) {
    return (
      <div className="text-sm text-[var(--text-tertiary)] flex items-center gap-2">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        Loading skills for selected agents…
      </div>
    );
  }
  if (allLoaded && totalSkills === 0) {
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
        const loaded = a.agent_id in skillsForAgents;
        const skills = skillsForAgents[a.agent_id] || [];
        if (!loaded) {
          return (
            <details key={a.agent_id} className="border border-[var(--border-default)]">
              <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center gap-2">
                <Loader2 className="w-3 h-3 animate-spin text-[var(--text-tertiary)]" />
                {a.name || a.agent_id} — loading skills…
              </summary>
            </details>
          );
        }
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
                    <div className={cn('grid grid-cols-4 gap-2', isReadOnly && 'opacity-60 pointer-events-none')}>
                      <RadioCard
                        label="URL install"
                        desc={hasUrl ? `${arch?.source_url}` : (choice?.install_method === 'url' && choice.source_url ? `(manual) ${choice.source_url}` : 'No URL recorded')}
                        disabled={isReadOnly}
                        active={choice?.install_method === 'url'}
                        onClick={() => setMethod({
                          skill_name: name, install_method: 'url',
                          source_url: arch?.source_url || choice?.source_url || '',
                          source_type: 'github', branch: 'main',
                        })}
                      />
                      <RadioCard
                        label="Zip install"
                        desc={hasZip ? `archive ${arch?.archive_path?.split('/').pop()}` : (choice?.install_method === 'zip' && choice.manual_zip_path ? `(manual) ${choice.manual_zip_path}` : 'No archive')}
                        disabled={isReadOnly}
                        active={choice?.install_method === 'zip'}
                        onClick={() => setMethod({
                          skill_name: name, install_method: 'zip',
                          archive_path: arch?.archive_path || choice?.archive_path || undefined,
                          manual_zip_path: choice?.manual_zip_path,
                        })}
                      />
                      <RadioCard
                        label="Full copy"
                        desc="⚠ includes wallets/credentials"
                        active={choice?.install_method === 'full_copy'}
                        disabled={isReadOnly}
                        onClick={() => setMethod({ skill_name: name, install_method: 'full_copy' })}
                      />
                      <RadioCard
                        label="Skip"
                        desc="Don't include this skill"
                        active={choice?.install_method === 'skip'}
                        disabled={isReadOnly}
                        onClick={() => setMethod({ skill_name: name, install_method: 'skip' })}
                      />
                    </div>
                    {/* Manual URL fill — shown when user picked URL but no archive */}
                    {!isReadOnly && choice?.install_method === 'url' && !hasUrl && (
                      <div className="mt-2 flex items-center gap-2">
                        <span className="text-[10px] text-[var(--text-tertiary)] shrink-0">GitHub URL:</span>
                        <input
                          type="text"
                          placeholder="https://github.com/owner/repo"
                          defaultValue={choice.source_url || ''}
                          onChange={(e) => setMethod({
                            skill_name: name, install_method: 'url',
                            source_url: e.target.value, source_type: 'github',
                            branch: choice.branch || 'main',
                          })}
                          className="flex-1 px-2 py-1 text-xs font-mono bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] focus:outline-none"
                        />
                        <input
                          type="text"
                          placeholder="branch"
                          defaultValue={choice.branch || 'main'}
                          onChange={(e) => setMethod({
                            skill_name: name, install_method: 'url',
                            source_url: choice.source_url || '', source_type: 'github',
                            branch: e.target.value || 'main',
                          })}
                          className="w-24 px-2 py-1 text-xs font-mono bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] focus:outline-none"
                        />
                      </div>
                    )}
                    {/* Manual zip upload — shown when user picked Zip but no archive */}
                    {!isReadOnly && choice?.install_method === 'zip' && !hasZip && (
                      <div className="mt-2 flex items-center gap-2">
                        <span className="text-[10px] text-[var(--text-tertiary)] shrink-0">Upload zip:</span>
                        <input
                          type="file"
                          accept=".zip"
                          onChange={async (e) => {
                            const f = e.target.files?.[0];
                            if (!f) return;
                            try {
                              await api.uploadSkillArchive({ skillName: name, sourceType: 'zip', file: f });
                              onAfterBackup();
                            } catch (err) {
                              console.error('manual zip upload failed', err);
                            }
                          }}
                          className="text-[11px]"
                        />
                        <span className="text-[10px] text-[var(--text-tertiary)]">
                          uploads to your skill_archives so subsequent exports reuse it
                        </span>
                      </div>
                    )}
                    {!hasUrl && !hasZip && !isReadOnly && choice?.install_method !== 'url' && choice?.install_method !== 'zip' && (
                      <div className="mt-2 text-[10px] text-[var(--text-tertiary)] flex items-start gap-1.5">
                        <AlertTriangle className="w-3 h-3 mt-0.5 text-[var(--color-yellow-500)] shrink-0" />
                        <span className="flex-1">
                          This skill has no archive. Pick URL/Zip to fill in a source manually,
                          ask the agent to back it up, choose Full copy (含 credentials), or Skip.
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
  agents, historyByAgent, excludedNarratives, excludedEvents, excludedJobs,
  onToggleNarrative, onToggleEvent, onToggleJob,
  onSelectAllNarratives, onSelectNoneNarratives,
  onSelectAllEventsInNarrative, onSelectNoneEventsInNarrative,
  onSelectAllJobsInNarrative, onSelectNoneJobsInNarrative,
  includeAll,
}: {
  agents: any[];
  historyByAgent: Record<string, ChatHistoryNarrative[]>;
  excludedNarratives: Record<string, Set<string>>;
  excludedEvents: Record<string, Set<string>>;
  excludedJobs: Record<string, Set<string>>;
  onToggleNarrative: (aid: string, nid: string) => void;
  onToggleEvent: (nid: string, eid: string) => void;
  onToggleJob: (nid: string, jid: string) => void;
  onSelectAllNarratives: (aid: string) => void;
  onSelectNoneNarratives: (aid: string) => void;
  onSelectAllEventsInNarrative: (nid: string) => void;
  onSelectNoneEventsInNarrative: (nid: string, allEventIds: string[]) => void;
  onSelectAllJobsInNarrative: (nid: string) => void;
  onSelectNoneJobsInNarrative: (nid: string, allJobIds: string[]) => void;
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
        // Distinguish "not loaded yet" from "loaded, empty":
        //   - undefined  → fetch still in flight, show Loading…
        //   - []         → fetched, this agent really has no narratives
        //   - non-empty  → render as usual
        const loaded = a.agent_id in historyByAgent;
        const narrs = historyByAgent[a.agent_id] || [];
        const exNars = excludedNarratives[a.agent_id] || new Set();
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <div className="flex items-center gap-2">
                {!loaded ? (
                  <span className="text-[10px] text-[var(--text-tertiary)] flex items-center gap-1">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    Loading…
                  </span>
                ) : (
                  <span className="text-[10px] text-[var(--text-tertiary)]">
                    {narrs.length - exNars.size} / {narrs.length} narratives
                  </span>
                )}
                <button
                  onClick={(e) => { e.preventDefault(); onSelectAllNarratives(a.agent_id); }}
                  disabled={!loaded}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40"
                >
                  Select all
                </button>
                <button
                  onClick={(e) => { e.preventDefault(); onSelectNoneNarratives(a.agent_id); }}
                  disabled={!loaded}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40"
                >
                  Select none
                </button>
              </div>
            </summary>
            <div className="p-2 max-h-[480px] overflow-y-auto space-y-2">
              {!loaded && (
                <div className="px-2 py-3 text-xs text-[var(--text-tertiary)] flex items-center gap-2">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Loading narratives + events + jobs from this agent…</span>
                </div>
              )}
              {loaded && narrs.length === 0 && (
                <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">No narratives.</div>
              )}
              {narrs.map((n) => {
                const narExcluded = exNars.has(n.narrative_id);
                const exEvts = excludedEvents[n.narrative_id] || new Set();
                const exJobs = excludedJobs[n.narrative_id] || new Set();
                const isOrphanJobsRow = n.narrative_id === '__orphan_jobs__';
                return (
                  <div key={n.narrative_id} className={cn(
                    'border border-[var(--border-subtle)]',
                    narExcluded && 'opacity-50'
                  )}>
                    <div className="px-3 py-2 flex items-center gap-2 bg-[var(--bg-secondary)]">
                      {!isOrphanJobsRow && (
                        <input
                          type="checkbox"
                          checked={!narExcluded}
                          onChange={() => onToggleNarrative(a.agent_id, n.narrative_id)}
                        />
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-mono truncate">{n.title}</div>
                        {(n.description || n.current_summary) && (
                          <div className="text-[11px] text-[var(--text-secondary)] line-clamp-2">
                            {n.description || n.current_summary}
                          </div>
                        )}
                        <div className="text-[10px] text-[var(--text-tertiary)] flex flex-wrap gap-x-2">
                          {!isOrphanJobsRow && (
                            <span>
                              {n.events.length - exEvts.size} / {n.events.length} events
                            </span>
                          )}
                          {n.jobs.length > 0 && (
                            <span>{n.jobs.length - exJobs.size} / {n.jobs.length} jobs</span>
                          )}
                          {n.type && <span>{n.type}</span>}
                          {(n.instances_count ?? 0) > 0 && (
                            <span>{n.instances_count} instance(s)</span>
                          )}
                          {n.created_at && <span>{(n.created_at || '').slice(0, 10)}</span>}
                          <span className="text-[var(--text-tertiary)]/70">{n.narrative_id}</span>
                        </div>
                      </div>
                      {!narExcluded && (n.events.length > 0 || n.jobs.length > 0) && (
                        <div className="flex items-center gap-1 shrink-0">
                          {n.events.length > 0 && (
                            <>
                              <button
                                onClick={(e) => { e.stopPropagation(); onSelectAllEventsInNarrative(n.narrative_id); }}
                                className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                              >
                                All events
                              </button>
                              <button
                                onClick={(e) => { e.stopPropagation(); onSelectNoneEventsInNarrative(n.narrative_id, n.events.map((x) => x.event_id)); }}
                                className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                              >
                                No events
                              </button>
                            </>
                          )}
                          {n.jobs.length > 0 && (
                            <>
                              <button
                                onClick={(e) => { e.stopPropagation(); onSelectAllJobsInNarrative(n.narrative_id); }}
                                className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                              >
                                All jobs
                              </button>
                              <button
                                onClick={(e) => { e.stopPropagation(); onSelectNoneJobsInNarrative(n.narrative_id, n.jobs.map((x) => x.job_id)); }}
                                className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                              >
                                No jobs
                              </button>
                            </>
                          )}
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
                    {!narExcluded && n.jobs.length > 0 && (
                      <div className="border-t border-[var(--border-subtle)] bg-[var(--bg-tertiary)]/30">
                        <div className="px-3 py-1 text-[10px] text-[var(--text-tertiary)] uppercase tracking-widest">
                          Jobs ({n.jobs.length - exJobs.size} / {n.jobs.length} included)
                        </div>
                        {n.jobs.map((j) => {
                          const jobExcluded = exJobs.has(j.job_id);
                          return (
                            <label key={j.job_id} className={cn(
                              'flex items-start gap-2 px-3 py-1 text-xs hover:bg-[var(--bg-tertiary)]',
                              jobExcluded && 'opacity-40'
                            )}>
                              <input
                                type="checkbox"
                                checked={!jobExcluded}
                                onChange={() => onToggleJob(n.narrative_id, j.job_id)}
                                className="mt-0.5"
                              />
                              <div className="flex-1 min-w-0">
                                <div className="font-mono text-[10px] text-[var(--text-tertiary)]">
                                  {j.job_type || 'job'} · {j.status || ''}
                                </div>
                                <div className="truncate">{j.title || j.job_id}</div>
                                {j.description && (
                                  <div className="text-[10px] text-[var(--text-tertiary)] truncate">{j.description}</div>
                                )}
                              </div>
                            </label>
                          );
                        })}
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
  selectedPageByAgent, onToggleSelectedPage,
  agents, onToggle, onBulkSet,
}: {
  entitiesByAgent: Record<string, SocialEntity[]>;
  selectedByAgent: Record<string, Set<string>>;
  pageByAgent: Record<string, number>;
  onTogglePage: (aid: string, page: number) => void;
  selectedPageByAgent: Record<string, number>;
  onToggleSelectedPage: (aid: string, page: number) => void;
  agents: any[];
  onToggle: (aid: string, eid: string) => void;
  onBulkSet: (aid: string, ids: string[]) => void;
  selectedTeam?: string;
  teams?: TeamWithMembers[];
}) {
  // Unified pagination — both the "All" and "Selected" panes show 30/page (P10).
  const PAGE_SIZE = 30;
  if (agents.length === 0) return (
    <div className="text-sm text-[var(--text-tertiary)]">Select agents first.</div>
  );
  return (
    <div className="space-y-4">
      {agents.map((a) => {
        const loaded = a.agent_id in entitiesByAgent;
        const list = (entitiesByAgent[a.agent_id] || []).slice().sort((x, y) =>
          (x.entity_name || x.entity_id).localeCompare(y.entity_name || y.entity_id)
        );
        const selected = selectedByAgent[a.agent_id] || new Set<string>();

        // Left pane (All entities) pagination
        const page = pageByAgent[a.agent_id] || 0;
        const totalPages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
        const safePage = Math.min(page, totalPages - 1);
        const slice = list.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

        // Right pane (Selected entities) pagination — sorted same way
        const selectedList = list.filter((e) => selected.has(e.entity_id));
        const selPage = selectedPageByAgent[a.agent_id] || 0;
        const selTotalPages = Math.max(1, Math.ceil(selectedList.length / PAGE_SIZE));
        const safeSelPage = Math.min(selPage, selTotalPages - 1);
        const selSlice = selectedList.slice(safeSelPage * PAGE_SIZE, (safeSelPage + 1) * PAGE_SIZE);

        if (!loaded) {
          return (
            <details key={a.agent_id} className="border border-[var(--border-default)]">
              <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center gap-2">
                <Loader2 className="w-3 h-3 animate-spin text-[var(--text-tertiary)]" />
                {a.name || a.agent_id} — loading social network entities…
              </summary>
            </details>
          );
        }
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  {selected.size} / {list.length} selected
                </span>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, list.map((x) => x.entity_id)); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={list.length === 0}
                >
                  Select all
                </button>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, []); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={selected.size === 0}
                >
                  Select none
                </button>
              </div>
            </summary>
            <div className="grid grid-cols-2 divide-x divide-[var(--border-subtle)]">
              {/* Left: all entities (paged 30/page) */}
              <div className="p-2">
                <div className="text-[10px] text-[var(--text-tertiary)] mb-1 px-2">
                  All entities (sort by name) — {list.length} total
                </div>
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
                  <Pager
                    page={safePage}
                    total={totalPages}
                    onPage={(p) => onTogglePage(a.agent_id, p)}
                  />
                )}
              </div>
              {/* Right: selected (paged 30/page) */}
              <div className="p-2">
                <div className="text-[10px] text-[var(--text-tertiary)] mb-1 px-2">
                  Selected (will be packaged) — {selectedList.length} total
                </div>
                {selSlice.length === 0 && (
                  <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">Nothing selected.</div>
                )}
                {selSlice.map((e) => (
                  <div key={e.entity_id} className="flex items-center gap-2 px-2 py-1 text-xs font-mono">
                    <Check className="w-3 h-3 text-[var(--color-green-500)] shrink-0" />
                    <span className="flex-1 truncate">{e.entity_name || e.entity_id}</span>
                    <span className="text-[9px] text-[var(--text-tertiary)]">[{e.entity_type}]</span>
                    <button onClick={() => onToggle(a.agent_id, e.entity_id)} className="text-[var(--color-red-500)] text-[10px]">remove</button>
                  </div>
                ))}
                {selTotalPages > 1 && (
                  <Pager
                    page={safeSelPage}
                    total={selTotalPages}
                    onPage={(p) => onToggleSelectedPage(a.agent_id, p)}
                  />
                )}
              </div>
            </div>
          </details>
        );
      })}
    </div>
  );
}

function Pager({ page, total, onPage }: { page: number; total: number; onPage: (p: number) => void }) {
  return (
    <div className="flex items-center justify-center gap-1 py-2 text-xs">
      <button
        onClick={() => onPage(Math.max(0, page - 1))}
        disabled={page === 0}
        className="px-2 py-0.5 border border-[var(--border-subtle)] disabled:opacity-30"
      >‹</button>
      <span>{page + 1} / {total}</span>
      <button
        onClick={() => onPage(Math.min(total - 1, page + 1))}
        disabled={page >= total - 1}
        className="px-2 py-0.5 border border-[var(--border-subtle)] disabled:opacity-30"
      >›</button>
    </div>
  );
}

function WorkspaceTab({
  filesByAgent, excludesByAgent, agents, onToggle, onBulkSet,
}: {
  filesByAgent: Record<string, { path: string; size: number; sensitive: boolean }[]>;
  excludesByAgent: Record<string, Set<string>>;
  agents: any[];
  onToggle: (aid: string, path: string) => void;
  // Dual-meaning bulk setter (see workspaceExcludes semantics):
  // include-all ⇒ excludesSet = {all sensitive paths}
  // include-non-sensitive-only ⇒ excludesSet = {} (default)
  // exclude-all ⇒ excludesSet = {all non-sensitive paths}
  onBulkSet: (aid: string, mode: 'all' | 'non-sensitive' | 'none') => void;
}) {
  if (agents.length === 0) return (<div className="text-sm text-[var(--text-tertiary)]">Select agents first.</div>);
  return (
    <div className="space-y-4">
      {agents.map((a) => {
        const loaded = a.agent_id in filesByAgent;
        const files = filesByAgent[a.agent_id] || [];
        const excludes = excludesByAgent[a.agent_id] || new Set();
        if (!loaded) {
          return (
            <details key={a.agent_id} className="border border-[var(--border-default)]">
              <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center gap-2">
                <Loader2 className="w-3 h-3 animate-spin text-[var(--text-tertiary)]" />
                {a.name || a.agent_id} — loading workspace files…
              </summary>
            </details>
          );
        }
        const sensitiveCount = files.filter((f) => f.sensitive).length;
        const includedCount = files.filter((f) =>
          f.sensitive ? excludes.has(f.path) : !excludes.has(f.path)
        ).length;
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  {includedCount} / {files.length} files included
                  {sensitiveCount > 0 && ` · ${sensitiveCount} sensitive`}
                </span>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, 'all'); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={files.length === 0}
                >
                  Include all
                </button>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, 'non-sensitive'); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={sensitiveCount === 0 && includedCount === files.length - 0}
                  title="Include all NON-sensitive files; sensitive files default-skipped"
                >
                  Defaults
                </button>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, 'none'); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={files.length === 0}
                >
                  Exclude all
                </button>
              </div>
            </summary>
            <div className="p-2 max-h-[320px] overflow-y-auto">
              {files.length === 0 && (
                <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">No workspace files reported by API.</div>
              )}
              {files.map((f) => {
                const sensitive = f.sensitive;
                const excluded = excludes.has(f.path);
                // Default include rules:
                //   - non-sensitive: included unless in `excludes` set
                //   - sensitive: excluded by default; included ONLY if user
                //     explicitly clicked through the confirm dialog (we track
                //     that as a presence in `excludes` toggled to false).
                // Implementation detail: `excludes` actually means "user
                // override". For non-sensitive files presence-in-excludes →
                // exclude. For sensitive files we INVERT the meaning: presence
                // means "user opted in" (included). This is hidden in the
                // toggle handler — sensitive files get a "really?" modal.
                const willBeIncluded = sensitive ? excluded : !excluded;
                return (
                  <label
                    key={f.path}
                    className={cn(
                      "flex items-center gap-2 px-2 py-1 hover:bg-[var(--bg-tertiary)]",
                      sensitive && "bg-[var(--color-yellow-500)]/10",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={willBeIncluded}
                      onChange={() => onToggle(a.agent_id, f.path)}
                    />
                    <span className={cn('text-xs font-mono flex-1 truncate', sensitive && 'text-[var(--color-yellow-500)]')}>
                      {f.path}
                    </span>
                    {sensitive && (
                      <span className="text-[9px] text-[var(--color-yellow-500)] uppercase tracking-wider font-mono">
                        {willBeIncluded ? 'sensitive — included' : 'sensitive — click to include'}
                      </span>
                    )}
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
  filename, mode,
}: any) {
  const skillStats = (skills || []).reduce(
    (acc: Record<string, number>, s: SkillExportSpec) => {
      const m = s.install_method || 'skip';
      acc[m] = (acc[m] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );
  const includedSkills = skills.filter(
    (s: SkillExportSpec) => s.install_method && s.install_method !== 'skip'
  ).length;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="w-[680px] max-w-[95vw] max-h-[90vh] bg-[var(--bg-primary)] border border-[var(--border-default)] flex flex-col">
        <div className="px-5 py-3 border-b border-[var(--border-default)] flex items-center justify-between">
          <h2 className="font-mono text-sm">Final review before download</h2>
          <span className="text-[10px] uppercase tracking-widest text-[var(--text-tertiary)]">
            {mode === 'full' ? 'Full snapshot' : 'Custom'}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4 text-sm font-mono">
          {/* Filename — show what will be downloaded */}
          <div className="text-[12px] flex items-center gap-2">
            <span className="text-[var(--text-tertiary)] uppercase tracking-widest text-[10px]">File:</span>
            <span className="text-[var(--text-primary)]">{filename || 'bundle.nxbundle'}</span>
          </div>
          <div>
            <div className="text-[var(--text-secondary)] uppercase text-xs mb-1">✓ Included</div>
            <ul className="list-disc list-inside text-[12px] text-[var(--text-secondary)] space-y-0.5">
              <li>{summary.agents} agent{summary.agents === 1 ? '' : 's'}</li>
              {team && <li>1 team "{team.team.name}"</li>}
              <li>
                {includedSkills} skill entr{includedSkills === 1 ? 'y' : 'ies'}:
                {' '}{skillStats.url || 0}× url,
                {' '}{skillStats.zip || 0}× zip,
                {' '}{skillStats.full_copy || 0}× full-copy
                {(skillStats.skip || 0) > 0 && `, ${skillStats.skip}× skip (NOT in bundle)`}
              </li>
              <li>{summary.socialEntities} social entit{summary.socialEntities === 1 ? 'y' : 'ies'}</li>
              <li>
                workspace files
                {mode === 'full'
                  ? ' (sensitive paths included)'
                  : ' (sensitive paths excluded by default — opt-in per file)'}
              </li>
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
