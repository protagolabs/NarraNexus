/**
 * @file_name: PdfRenderer.tsx
 * @description: Lazy-loaded renderer for application/pdf artifacts.
 *
 * Uses <object data type="application/pdf"> rather than the HtmlRenderer
 * iframe sandbox pattern. PDF rendering is plugin-based across browsers —
 * Chromium uses PDFium, Firefox uses PDF.js (which needs same-origin XHR),
 * and WebKit/WKWebView has its own Preview-based viewer. The sandboxed iframe
 * approach (sandbox="allow-scripts" without allow-same-origin) breaks Firefox's
 * PDF.js because it requires same-origin XHR to load its own worker modules.
 * Chromium's PDFium silently ignores the sandbox attribute for plugin content,
 * making the sandbox a false safety guarantee. WKWebView (macOS/iOS desktop)
 * behaves differently still.
 *
 * <object> with an explicit MIME type lets each browser pick its native PDF
 * renderer. The response CSP on /raw for PDF kind remains
 * "default-src 'none'; object-src 'self'" which still blocks embedded actions.
 */

import type { Artifact } from '@/types/artifact';
import { rawUrl } from '@/types/artifact';

interface Props {
  artifact: Artifact;
  version: number;
}

export default function PdfRenderer({ artifact, version }: Props) {
  const src = rawUrl(artifact.agent_id, artifact.artifact_id, version);
  return (
    <object
      data={src}
      type="application/pdf"
      className="w-full h-full border-0"
      aria-label={artifact.title}
    >
      <div className="p-4 opacity-60">
        Your browser cannot display this PDF inline.&nbsp;
        <a href={src} target="_blank" rel="noopener noreferrer" className="underline">
          Open it in a new tab
        </a>
      </div>
    </object>
  );
}
