/**
 * @file_name: ImageRenderer.tsx
 * @description: Lazy-loaded renderer for image artifacts (image/png, image/jpeg).
 *
 * Fetches the raw artifact bytes via the authenticated raw URL and renders
 * them in a centred, object-contain <img>. No client-side fetch needed —
 * the browser handles the GET via the proxy so cookies/auth headers apply.
 */

import type { Artifact } from '@/types/artifact';
import { rawUrl } from '@/types/artifact';

interface Props {
  artifact: Artifact;
  version: number;
}

export default function ImageRenderer({ artifact, version }: Props) {
  return (
    <div className="w-full h-full flex items-center justify-center bg-[var(--bg-deep)] p-4">
      <img
        src={rawUrl(artifact.agent_id, artifact.artifact_id, version)}
        alt={artifact.title}
        className="max-w-full max-h-full object-contain"
      />
    </div>
  );
}
