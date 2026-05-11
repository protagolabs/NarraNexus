/**
 * @file_name: ImageRenderer.tsx
 * @description: Lazy-loaded renderer for image artifacts (image/png, image/jpeg).
 *
 * Fetches the raw artifact bytes through fetch() so the JWT in the
 * Authorization header is attached (cloud-mode auth middleware rejects
 * /api/* without it, and a native <img src=...> can't carry headers).
 * The fetched blob is converted to a blob: URL and handed to <img>.
 */

import { useEffect, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { rawUrl } from '@/types/artifact';
import { fetchArtifactBlobUrl } from '@/services/artifactsApi';

interface Props {
  artifact: Artifact;
  version: number;
}

export default function ImageRenderer({ artifact, version }: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    (async () => {
      setError(null);
      try {
        const url = await fetchArtifactBlobUrl(
          rawUrl(artifact.agent_id, artifact.artifact_id, version),
        );
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        createdUrl = url;
        setSrc(url);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [artifact.agent_id, artifact.artifact_id, version]);

  if (error) return <div className="p-4 text-red-400">Failed to load: {error}</div>;
  if (!src) return <div className="p-4 opacity-60">Loading…</div>;

  return (
    <div className="w-full h-full flex items-center justify-center bg-[var(--bg-deep)] p-4">
      <img
        src={src}
        alt={artifact.title}
        className="max-w-full max-h-full object-contain"
      />
    </div>
  );
}
