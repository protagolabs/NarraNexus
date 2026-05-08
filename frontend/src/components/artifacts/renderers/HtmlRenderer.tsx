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
 *   src = /api/.../raw               — FastAPI's CSP response header applies
 *                                       (default-src 'none' blocks all outbound fetch/script src)
 *   referrerPolicy = no-referrer     — no origin leak to any destination
 *
 * Why allow-scripts but no allow-same-origin?
 *   With allow-same-origin the iframe shares the parent origin and can read
 *   parent localStorage, cookies, and DOM — a trivial XSS escape. Without it,
 *   the iframe is null-origin and completely isolated. allow-scripts is needed
 *   for chart libraries and interactive visualisations that the agent may emit.
 *   The two flags are intentionally mutually exclusive here.
 *
 * Why src= instead of srcdoc=?
 *   srcdoc content is inline-parsed with the parent origin, so CSP headers
 *   from the server do not apply. Using src= means the browser performs a real
 *   HTTP request and the FastAPI route can set Content-Security-Policy on the
 *   response, further blocking outbound network calls even if the iframe's
 *   null-origin sandbox is ever misconfigured.
 */

import type { Artifact } from '@/types/artifact';
import { rawUrl } from '@/types/artifact';

interface Props {
  artifact: Artifact;
  version: number;
}

export default function HtmlRenderer({ artifact, version }: Props) {
  return (
    <iframe
      title={artifact.title}
      sandbox="allow-scripts"
      src={rawUrl(artifact.agent_id, artifact.artifact_id, version)}
      referrerPolicy="no-referrer"
      loading="lazy"
      className="w-full h-full border-0 bg-white"
    />
  );
}
