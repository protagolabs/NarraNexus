/**
 * @file_name: ArtifactTabStrip.tsx
 * @description: Horizontally scrolling tab bar showing the user's currently
 * VISIBLE artifacts — i.e. artifacts that are not minimized. Minimized
 * artifacts surface in the header bar above (rendered by ArtifactColumn).
 *
 * Per-tab actions:
 *   📌 / 📍  pin / unpin (status toggle, persisted to DB)
 *   ─        minimize (frontend-only hide, persisted to localStorage; the
 *            artifact stays in the DB and can be restored from the header)
 *
 * Permanent deletion is intentionally NOT on the tab — it lives in the
 * download menu (ArtifactDownloadMenu) behind a confirm dialog so a casual
 * tab close can never destroy the underlying file.
 */

import { Minus } from 'lucide-react';
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
  const pin = useArtifactStore((s) => s.pin);
  const minimizeTab = useArtifactStore((s) => s.minimizeTab);

  const visible = artifacts.filter((a) => !minimizedTabIds.has(a.artifact_id));

  if (visible.length === 0) {
    return <div className="text-xs opacity-50 px-3 py-2">No artifacts yet</div>;
  }

  return (
    <div className="flex flex-row overflow-x-auto border-b border-[var(--border-default)]">
      {visible.map((a) => (
        <TabButton
          key={a.artifact_id}
          artifact={a}
          active={a.artifact_id === activeId}
          onClick={() => setActive(a.artifact_id)}
          onPin={() => pin(agentId, a.artifact_id, !a.pinned)}
          onMinimize={() => minimizeTab(a.artifact_id)}
        />
      ))}
    </div>
  );
}

function TabButton({
  artifact, active, onClick, onPin, onMinimize,
}: {
  artifact: Artifact;
  active: boolean;
  onClick: () => void;
  onPin: () => void;
  onMinimize: () => void;
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
        onClick={(e) => { e.stopPropagation(); onPin(); }}
        title={artifact.pinned ? 'Unpin' : 'Pin'}
        className="text-xs opacity-60 hover:opacity-100"
      >
        {artifact.pinned ? '📌' : '📍'}
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); onMinimize(); }}
        title="最小化（不会删除文件，可从顶部恢复）"
        className="p-1 rounded opacity-60 hover:opacity-100 hover:bg-[var(--bg-secondary)] transition-colors"
        aria-label="Minimize tab"
      >
        <Minus className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
