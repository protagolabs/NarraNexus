/**
 * @file_name: OfficeRenderer.tsx
 * @description: Renderer for Office artifacts (.docx / .xlsx / .pptx).
 *
 * An Office artifact's entry pointer IS the original office file (so the
 * download menu's "download original" grabs the real .docx/.xlsx/.pptx). The
 * visual preview is a sibling HTML snapshot the backend OfficeModule generated
 * with `officecli view <file> html -o <stem>.preview.html`, living in the same
 * artifact-root directory as the entry.
 *
 * This renderer derives that sibling name from the entry `file_path`
 * (`slides.pptx` → `slides.preview.html`), fetches it through the token-
 * protected raw route, and renders it as a blob URL inside a sandboxed iframe.
 *
 * Why blob (not a raw-URL iframe like the multi-file HtmlRenderer path)?
 *   OfficeCLI's `view html` output is a static, self-contained snapshot, so it
 *   needs no sibling-asset resolution — and a blob URL is same-origin to the
 *   parent, sidestepping Tauri's WKWebView mixed-content block uniformly across
 *   desktop and web.
 *
 * Security: same sandbox contract as HtmlRenderer — `allow-scripts`, no
 * `allow-same-origin`, no top-navigation, no-referrer.
 *
 * NOTE: the `<stem>.preview.html` convention MUST match `preview_name_for` in
 * the backend officecli_client.py. Keep the two in sync.
 */

import { useEffect, useState } from 'react';

import type { Artifact } from '@/types/artifact';
import { useArtifactRawUrl } from '@/hooks/useArtifactRawUrl';
import { fetchArtifactBlobUrl } from '@/services/artifactsApi';
import { isTauri, fetchArtifactViaTauri } from '@/lib/tauri';

interface Props {
  artifact: Artifact;
}

/** `.../slides.pptx` → `slides.preview.html` (sibling in the same dir). */
function previewSiblingName(filePath: string): string {
  const base = filePath.split('/').filter(Boolean).pop() ?? '';
  const stem = base.replace(/\.[^.]+$/, '');
  return `${stem}.preview.html`;
}

export default function OfficeRenderer({ artifact }: Props) {
  // refreshKey = updated_at: re-registering (office_render with
  // target_artifact_id) bumps updated_at → re-mint the token → refetch the
  // regenerated preview.
  const { url, error } = useArtifactRawUrl(
    artifact.agent_id,
    artifact.artifact_id,
    artifact.updated_at,
  );
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [blobError, setBlobError] = useState<string | null>(null);

  useEffect(() => {
    if (!url) {
      setBlobUrl(null);
      setBlobError(null);
      return;
    }
    const previewUrl = `${url}${previewSiblingName(artifact.file_path)}`;

    let cancelled = false;
    let nextBlobUrl: string | null = null;
    setBlobUrl(null);
    setBlobError(null);
    (async () => {
      try {
        let out: string | null = null;
        if (isTauri()) {
          out = await fetchArtifactViaTauri(previewUrl);
        }
        if (!out) {
          out = await fetchArtifactBlobUrl(previewUrl);
        }
        if (!cancelled && out) {
          nextBlobUrl = out;
          setBlobUrl(out);
        } else if (!cancelled && !out) {
          setBlobError('Preview unavailable.');
        }
      } catch (e) {
        if (!cancelled) setBlobError(String(e));
      }
    })();

    return () => {
      cancelled = true;
      if (nextBlobUrl) URL.revokeObjectURL(nextBlobUrl);
    };
  }, [url, artifact.file_path]);

  if (error || blobError) {
    return (
      <div className="p-4 text-red-400">
        Failed to load preview: {error || blobError}
        <div className="mt-2 text-xs opacity-70">
          Ask the agent to run <code>office_render</code> on this document again.
        </div>
      </div>
    );
  }
  if (!url || !blobUrl) {
    return <div className="p-4 opacity-60">Loading…</div>;
  }

  return (
    <iframe
      key={artifact.updated_at}
      title={artifact.title}
      sandbox="allow-scripts"
      src={blobUrl}
      referrerPolicy="no-referrer"
      loading="lazy"
      className="w-full h-full border-0 bg-white"
    />
  );
}
