/**
 * @file_name: ArtifactPreviewCard.tsx
 * @description: Inline thumbnail card rendered inside chat messages when a tool
 * result references an artifact. Clicking opens the ArtifactColumn and focuses
 * the corresponding tab.
 *
 * Renders real thumbnails for image/csv/markdown; placeholder labels for
 * chart/html/pdf so the chat thread does not have to fetch the full artifact
 * eagerly for kinds that require complex rendering environments.
 */

import { useEffect, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { rawUrl } from '@/types/artifact';
import { useArtifactStore } from '@/stores';

interface Props {
  artifact: Artifact;
}

export default function ArtifactPreviewCard({ artifact }: Props) {
  const setActive = useArtifactStore((s) => s.setActive);
  const setCollapsed = useArtifactStore((s) => s.setCollapsed);
  const [csvHead, setCsvHead] = useState<string[][] | null>(null);
  const [mdHead, setMdHead] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    if (artifact.kind !== 'text/csv' && artifact.kind !== 'text/markdown') return;
    const url = rawUrl(artifact.agent_id, artifact.artifact_id, artifact.latest_version);
    // Wrap in async IIFE so all setState calls — including the reset — happen
    // inside the same async microtask batch, satisfying react-hooks/set-state-in-effect.
    (async () => {
      setPreviewError(null);
      try {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const t = await r.text();
        if (artifact.kind === 'text/csv') {
          setCsvHead(t.split(/\r?\n/).slice(0, 5).map((row) => row.split(',')));
        } else {
          setMdHead(t.slice(0, 200) + (t.length > 200 ? '…' : ''));
        }
      } catch (e) {
        setPreviewError(String(e));
      }
    })();
  }, [artifact.kind, artifact.agent_id, artifact.artifact_id, artifact.latest_version]);

  const open = () => {
    setCollapsed(false);
    setActive(artifact.artifact_id);
  };

  return (
    <button
      onClick={open}
      className="w-full max-w-md flex flex-col gap-2 p-3 border border-[var(--border-default)] bg-[var(--bg-primary)] hover:bg-[var(--bg-secondary)] text-left"
    >
      <div className="text-xs uppercase opacity-60">{artifact.kind}</div>
      <div className="text-sm font-semibold">{artifact.title}</div>
      <div className="min-h-[80px]">
        {(artifact.kind === 'image/png' || artifact.kind === 'image/jpeg') && (
          <img
            src={rawUrl(artifact.agent_id, artifact.artifact_id, artifact.latest_version)}
            alt={artifact.title}
            className="max-h-24 object-contain"
          />
        )}
        {artifact.kind === 'text/csv' && csvHead && (
          <table className="text-xs border-collapse">
            <tbody>
              {csvHead.map((row, i) => (
                <tr key={i}>
                  {row.slice(0, 5).map((c, j) => (
                    <td key={j} className="border border-[var(--border-default)] px-1">
                      {c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {artifact.kind === 'text/markdown' && mdHead && (
          <p className="text-xs opacity-80 whitespace-pre-line">{mdHead}</p>
        )}
        {artifact.kind === 'application/vnd.echarts+json' && (
          <p className="text-xs opacity-60">[chart preview — open tab to view]</p>
        )}
        {artifact.kind === 'text/html' && (
          <p className="text-xs opacity-60">[HTML app — open tab to view]</p>
        )}
        {artifact.kind === 'application/pdf' && (
          <p className="text-xs opacity-60">[PDF document — open tab to view]</p>
        )}
      </div>
      {previewError && (
        <p className="text-xs text-red-400/80">Preview unavailable: {previewError}</p>
      )}
      <div className="text-xs opacity-50">Open →</div>
    </button>
  );
}
