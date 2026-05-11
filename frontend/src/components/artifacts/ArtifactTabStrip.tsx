/**
 * @file_name: ArtifactTabStrip.tsx
 * @description: Horizontally scrolling tab bar showing the user's currently
 * VISIBLE artifacts — i.e. artifacts that are not minimized. Minimized
 * artifacts surface in the header bar above (rendered by ArtifactColumn).
 *
 * Per-tab actions:
 *   ─   minimize (frontend-only hide, persisted to localStorage; the
 *       artifact stays in the DB and can be restored from the header)
 *   🗑️  delete permanently (with confirm; rmtree + DB delete)
 *
 * Pin/unpin is intentionally NOT exposed: under the current LLM-driven flow
 * every agent-emitted artifact is auto-pinned at creation (C1 fix), and the
 * route refuses to unpin an artifact whose original_session_id is null
 * (C1.5 guard, prevents the limbo state). So the toggle has no working
 * outcome in v1. When session-scoped artifacts become reachable from the UI
 * (loadForSession wiring), the pin toggle can come back.
 */

import { Minus, Trash2 } from 'lucide-react';
import { useArtifactStore } from '@/stores';
import type { Artifact } from '@/types/artifact';

interface Props {
  agentId: string;
}

export default function ArtifactTabStrip({ agentId }: Props) {
  const artifacts = useArtifactStore((s) => s.artifacts);
  const minimizedTabIds = useArtifactStore((s) => s.minimizedTabIds);
  const activeId = useArtifactStore((s) => s.activeArtifactId);
  const setActive = useArtifactStore((s) => s.setActive);
  const minimizeTab = useArtifactStore((s) => s.minimizeTab);
  const deleteArtifact = useArtifactStore((s) => s.delete);

  const visible = artifacts.filter((a) => !minimizedTabIds.has(a.artifact_id));

  if (visible.length === 0) {
    return <div className="text-xs opacity-50 px-3 py-2">No artifacts yet</div>;
  }

  const handleDelete = (artifact: Artifact) => {
    const ok = window.confirm(
      `Permanently delete "${artifact.title}"?\n\n` +
      'This removes the file from disk AND the database record. Cannot be undone.\n\n' +
      'If you only want to hide the tab, use the "−" minimize button next to it instead.',
    );
    if (!ok) return;
    deleteArtifact(agentId, artifact.artifact_id).catch((e) => {
      window.alert(`Delete failed: ${e}`);
    });
  };

  return (
    <div className="flex flex-row overflow-x-auto border-b border-[var(--border-default)]">
      {visible.map((a) => (
        <TabButton
          key={a.artifact_id}
          artifact={a}
          active={a.artifact_id === activeId}
          onClick={() => setActive(a.artifact_id)}
          onMinimize={() => minimizeTab(a.artifact_id)}
          onDelete={() => handleDelete(a)}
        />
      ))}
    </div>
  );
}

function TabButton({
  artifact, active, onClick, onMinimize, onDelete,
}: {
  artifact: Artifact;
  active: boolean;
  onClick: () => void;
  onMinimize: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className={
        'flex items-center gap-2 px-3 py-2 cursor-pointer border-r border-[var(--border-default)] ' +
        (active ? 'bg-[var(--bg-primary)]' : 'opacity-70 hover:opacity-100')
      }
    >
      <span className="text-sm truncate max-w-[12rem]">{artifact.title}</span>
      <button
        onClick={(e) => { e.stopPropagation(); onMinimize(); }}
        title="Minimize (does not delete; restore from the bar above)"
        className="p-1 rounded opacity-60 hover:opacity-100 hover:bg-[var(--bg-secondary)] transition-colors"
        aria-label="Minimize tab"
      >
        <Minus className="w-3.5 h-3.5" />
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        title="Delete permanently (removes file and DB record, cannot be undone)"
        className="p-1 rounded opacity-60 hover:opacity-100 hover:bg-red-900/40 hover:text-red-400 transition-colors"
        aria-label="Delete artifact permanently"
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
