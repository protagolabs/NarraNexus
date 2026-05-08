/**
 * @file_name: ArtifactTabStrip.tsx
 * @description: Horizontally scrolling tab bar showing all open artifacts for an
 * agent session. Each tab supports pin and close actions. The strip is the
 * navigation control for ArtifactColumn — clicking a tab activates it in the
 * store which triggers the renderer dispatch.
 *
 * Pin semantics: a pinned artifact persists across sessions (session_id becomes
 * null on the server). The pin emoji (📌 = pinned, 📍 = unpinned) is a quick
 * mnemonic: 📌 looks "stuck in place" (already pinned), 📍 looks like it is
 * waiting to be planted (not yet pinned).
 */

import { useArtifactStore } from '@/stores';
import type { Artifact } from '@/types/artifact';

interface Props {
  agentId: string;
}

export default function ArtifactTabStrip({ agentId }: Props) {
  const artifacts = useArtifactStore((s) => s.artifacts);
  const activeId = useArtifactStore((s) => s.activeArtifactId);
  const setActive = useArtifactStore((s) => s.setActive);
  const pin = useArtifactStore((s) => s.pin);
  const remove = useArtifactStore((s) => s.delete);

  if (artifacts.length === 0) {
    return <div className="text-xs opacity-50 px-3 py-2">No artifacts yet</div>;
  }

  return (
    <div className="flex flex-row overflow-x-auto border-b border-[var(--border-default)]">
      {artifacts.map((a) => (
        <TabButton
          key={a.artifact_id}
          artifact={a}
          active={a.artifact_id === activeId}
          onClick={() => setActive(a.artifact_id)}
          onPin={() => pin(agentId, a.artifact_id, !a.pinned)}
          onClose={() => remove(agentId, a.artifact_id)}
        />
      ))}
    </div>
  );
}

function TabButton({
  artifact, active, onClick, onPin, onClose,
}: {
  artifact: Artifact;
  active: boolean;
  onClick: () => void;
  onPin: () => void;
  onClose: () => void;
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
        onClick={(e) => { e.stopPropagation(); onClose(); }}
        title="Delete"
        className="text-xs opacity-60 hover:opacity-100"
      >
        ✕
      </button>
    </div>
  );
}
