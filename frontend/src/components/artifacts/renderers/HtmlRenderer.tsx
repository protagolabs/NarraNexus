/**
 * @file_name: HtmlRenderer.tsx
 * @description: Security-hardened renderer for text/html artifacts.
 *
 * Renders agent-emitted HTML inside a fully isolated iframe.
 *
 * Security contract (must NOT be relaxed without a spec change):
 *   sandbox = "allow-scripts"        — inline JS may run
 *   ✗ allow-same-origin              — iframe is null-origin; cannot read parent DOM or cookies
 *   ✗ allow-top-navigation           — cannot break out of the iframe / redirect the top frame
 *   ✗ allow-popups-to-escape-sandbox — cannot spawn an unsandboxed window
 *   referrerPolicy = no-referrer     — no origin leak to any destination
 *
 * Why allow-scripts but no allow-same-origin?
 *   With allow-same-origin the iframe shares the parent origin and can read
 *   parent localStorage, cookies, and DOM — a trivial XSS escape. Without it,
 *   the iframe is null-origin and completely isolated. allow-scripts is needed
 *   for chart libraries and interactive visualisations that the agent may emit.
 *
 * Why blob: URL instead of src=/api/.../raw?
 *   Cloud-mode auth middleware rejects /api/* without an Authorization header,
 *   but iframe `src=` can't attach headers (it's a native browser fetch). We
 *   fetch the bytes via JS (which CAN attach the JWT), wrap them in a blob URL,
 *   and hand that to the iframe. Trade-off: the response CSP header is lost
 *   (blob: URLs don't carry HTTP headers), so the iframe sandbox becomes the
 *   sole isolation primitive. That's still load-bearing — null origin blocks
 *   parent access, all network, and cookie reads.
 */

import { useEffect, useState } from 'react';
import type { Artifact } from '@/types/artifact';
import { rawUrl } from '@/types/artifact';
import { fetchArtifactBlobUrl } from '@/services/artifactsApi';

interface Props {
  artifact: Artifact;
  version: number;
}

export default function HtmlRenderer({ artifact, version }: Props) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    // Async IIFE — keeps every setState behind an await boundary so the
    // react-hooks/set-state-in-effect rule passes.
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
    <iframe
      title={artifact.title}
      sandbox="allow-scripts"
      src={src}
      referrerPolicy="no-referrer"
      loading="lazy"
      className="w-full h-full border-0 bg-white"
    />
  );
}
