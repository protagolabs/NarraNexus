/**
 * @file_name: ArtifactColumn.tsx
 * @description: The 4th column in the agent layout. Hosts ArtifactTabStrip plus a
 * lazy-rendered content area that dispatches to the appropriate renderer by
 * artifact kind. Collapses to a sliver button when the user dismisses it;
 * disappears entirely when no artifacts are loaded.
 *
 * Renderer dispatch uses React.lazy so each renderer bundle is only downloaded
 * when the corresponding kind is first activated — avoids pulling ECharts or
 * PDF viewer code on first load.
 *
 * PDF artifacts use a dedicated PdfRenderer based on <object> rather than an
 * iframe sandbox, because PDF.js in Firefox requires same-origin XHR and the
 * sandboxed iframe pattern breaks it. See PdfRenderer.tsx for the full rationale.
 */

import { lazy, Suspense } from 'react';
import { useArtifactStore } from '@/stores';
import type { Artifact, ArtifactKind } from '@/types/artifact';
import ArtifactTabStrip from './ArtifactTabStrip';

const HtmlRenderer = lazy(() => import('./renderers/HtmlRenderer'));
const ChartRenderer = lazy(() => import('./renderers/ChartRenderer'));
const CsvRenderer = lazy(() => import('./renderers/CsvRenderer'));
const ImageRenderer = lazy(() => import('./renderers/ImageRenderer'));
const MarkdownRenderer = lazy(() => import('./renderers/MarkdownRenderer'));
const PdfRenderer = lazy(() => import('./renderers/PdfRenderer'));

type RendererComponent = React.LazyExoticComponent<
  React.ComponentType<{ artifact: Artifact; version: number }>
>;

const RENDERER_BY_KIND: Record<ArtifactKind, RendererComponent> = {
  'text/html': HtmlRenderer,
  'application/vnd.echarts+json': ChartRenderer,
  'text/csv': CsvRenderer,
  'text/markdown': MarkdownRenderer,
  'image/png': ImageRenderer,
  'image/jpeg': ImageRenderer,
  // PDF: dedicated PdfRenderer uses <object> instead of the sandboxed iframe
  // to avoid breaking Firefox PDF.js (needs same-origin XHR) and WKWebView.
  'application/pdf': PdfRenderer,
};

interface Props {
  agentId: string;
}

export default function ArtifactColumn({ agentId }: Props) {
  const artifacts = useArtifactStore((s) => s.artifacts);
  const activeId = useArtifactStore((s) => s.activeArtifactId);
  const collapsed = useArtifactStore((s) => s.collapsed);
  const setCollapsed = useArtifactStore((s) => s.setCollapsed);

  if (artifacts.length === 0) return null;

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="w-8 border-l border-[var(--border-default)] [writing-mode:vertical-rl] text-xs"
        title="Expand artifacts"
      >
        ▶ Artifacts ({artifacts.length})
      </button>
    );
  }

  const active = artifacts.find((a) => a.artifact_id === activeId);
  const Renderer = active ? RENDERER_BY_KIND[active.kind] : null;

  return (
    <aside className="flex flex-col min-w-[320px] flex-[2] border-l border-[var(--border-default)] bg-[var(--bg-primary)]">
      {/* No border-b here — ArtifactTabStrip already provides one */}
      <div className="flex items-center justify-between">
        <ArtifactTabStrip agentId={agentId} />
        <button
          onClick={() => setCollapsed(true)}
          className="text-xs opacity-60 hover:opacity-100 px-2"
          title="Collapse"
        >
          ▶
        </button>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {active && Renderer ? (
          <Suspense fallback={<div className="p-4 opacity-60">Loading renderer…</div>}>
            <Renderer artifact={active} version={active.latest_version} />
          </Suspense>
        ) : (
          <div className="p-4 opacity-60">Select an artifact</div>
        )}
      </div>
    </aside>
  );
}
