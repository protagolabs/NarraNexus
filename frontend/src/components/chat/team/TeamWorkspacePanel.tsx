/**
 * @file_name: TeamWorkspacePanel.tsx
 * @author: NarraNexus
 * @date: 2026-08-07
 * @description: The team room's workspace — artifacts and shared files.
 *
 * Until now a team's output had nowhere to live. An artifact registered during
 * a team turn appeared only in the producing agent's private chat, so seeing
 * "what the team made" meant leaving the room and opening one member's
 * one-to-one conversation; shared files had no UI at all, and the only way to
 * find one was an agent reciting an absolute path into the chat.
 *
 * One panel, two tabs, rather than two entry points: the mental model is "the
 * team's workspace", and splitting artifacts from files would ask the user to
 * know which kind of thing they are looking for before they can look.
 *
 * Every row is attributed. In a one-to-one chat "who made this" is never a
 * question; here several agents write into the same space, and an unattributed
 * list is how a shared workspace turns into an anonymous pile.
 */

import { useCallback, useEffect, useState } from 'react';

import ArtifactRenderer from '@/components/artifacts/ArtifactRenderer';
import { api } from '@/lib/api';
import type { Artifact, TeamFile } from '@/types/artifact';

interface TeamWorkspacePanelProps {
  teamId: string;
  /** Bumped by the parent when a turn ends, so the panel refetches. */
  refreshKey?: number;
}

type Tab = 'artifacts' | 'files';

function formatSize(bytes: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** "2h ago" style — absolute timestamps read as noise in a list this dense. */
function formatWhen(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function TeamWorkspacePanel({ teamId, refreshKey = 0 }: TeamWorkspacePanelProps) {
  const [tab, setTab] = useState<Tab>('artifacts');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [files, setFiles] = useState<TeamFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Both tabs load together: the counts are part of the tab labels, so
      // fetching lazily would make the un-opened tab claim zero items.
      const [a, f] = await Promise.all([
        api.listTeamArtifacts(teamId),
        api.listTeamFiles(teamId),
      ]);
      setArtifacts(a);
      setFiles(f);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load workspace');
    } finally {
      setLoading(false);
    }
  }, [teamId]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      if (alive) await load();
    })();
    return () => {
      alive = false;
    };
  }, [load, refreshKey]);

  // Rendered in place rather than opened in a new tab: the panel exists so a
  // team's output can be read WHILE reading the conversation that produced it,
  // and a new tab throws that context away.
  //
  // ArtifactRenderer needs no team plumbing. It mints through the agent-scoped
  // route using the artifact's own agent_id — the producer — and that route
  // authorises on "does the JWT user own this agent". Every member of a team is
  // an agent of the team's single owner, so a teammate's artifact resolves
  // without a team-specific token path.
  const selected = artifacts.find((a) => a.artifact_id === selectedId) ?? null;

  const tabs: Array<{ id: Tab; label: string; count: number }> = [
    { id: 'artifacts', label: 'Artifacts', count: artifacts.length },
    { id: 'files', label: 'Files', count: files.length },
  ];

  return (
    <div className="flex h-full w-72 shrink-0 flex-col border-l border-[var(--nm-hairline)]">
      <div className="shrink-0 flex items-center gap-1 px-3 py-2 border-b border-[var(--nm-hairline)]">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`px-2 py-1 text-[11px] font-mono uppercase tracking-wider rounded ${
              tab === t.id
                ? 'text-[var(--nm-ink)] bg-[var(--nm-hairline)]'
                : 'text-[var(--text-tertiary)]'
            }`}
          >
            {t.label} {t.count > 0 && <span className="opacity-60">{t.count}</span>}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {error && (
          <div className="px-3 py-2 text-[11px] text-[var(--nm-danger,#c0392b)]">{error}</div>
        )}

        {tab === 'artifacts' &&
          (artifacts.length === 0 ? (
            <EmptyState
              loading={loading}
              // Names the mechanism rather than just saying "empty": the user's
              // next question is always "how does something get in here".
              hint="Artifacts your team's agents register in this room appear here."
            />
          ) : (
            artifacts.map((a) => (
              <button
                key={a.artifact_id}
                type="button"
                onClick={() => setSelectedId(a.artifact_id)}
                className={`w-full text-left px-3 py-2 border-b border-[var(--nm-hairline)] hover:bg-[var(--nm-hairline)] ${
                  selectedId === a.artifact_id ? 'bg-[var(--nm-hairline)]' : ''
                }`}
              >
                <div className="text-xs text-[var(--nm-ink)] truncate">{a.title}</div>
                <div className="mt-0.5 text-[10px] font-mono text-[var(--text-tertiary)] truncate">
                  {a.agent_id} · {formatWhen(a.updated_at)}
                </div>
              </button>
            ))
          ))}

        {tab === 'files' &&
          (files.length === 0 ? (
            <EmptyState
              loading={loading}
              hint="Files shared with bus_share_to_team appear here."
            />
          ) : (
            files.map((f) => (
              <div
                key={f.file_id}
                className="px-3 py-2 border-b border-[var(--nm-hairline)]"
              >
                <div className="text-xs text-[var(--nm-ink)] truncate">{f.original_name}</div>
                <div className="mt-0.5 text-[10px] font-mono text-[var(--text-tertiary)] truncate">
                  {f.shared_by_agent_id} · {formatWhen(f.created_at)}
                  {f.size_bytes ? ` · ${formatSize(f.size_bytes)}` : ''}
                </div>
              </div>
            ))
          ))}
      </div>

      {selected && (
        <div className="shrink-0 h-64 border-t border-[var(--nm-hairline)] flex flex-col min-h-0">
          <div className="shrink-0 flex items-center justify-between px-3 py-1.5 border-b border-[var(--nm-hairline)]">
            <span className="text-[10px] font-mono text-[var(--text-tertiary)] truncate">
              {selected.title}
            </span>
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="text-[10px] font-mono text-[var(--text-tertiary)] hover:text-[var(--nm-ink)]"
            >
              close
            </button>
          </div>
          <div className="flex-1 min-h-0 overflow-hidden">
            <ArtifactRenderer artifact={selected} />
          </div>
        </div>
      )}
    </div>
  );
}

function EmptyState({ loading, hint }: { loading: boolean; hint: string }) {
  return (
    <div className="px-3 py-6 text-[11px] text-[var(--text-tertiary)]">
      {loading ? 'Loading…' : hint}
    </div>
  );
}
