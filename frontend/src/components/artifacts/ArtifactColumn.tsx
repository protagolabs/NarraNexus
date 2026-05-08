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
 * PDF artifacts reuse HtmlRenderer: the raw endpoint serves the PDF bytes with
 * an appropriate Content-Type, which triggers the browser's native PDF viewer
 * inside the iframe. This reuse gives PDF the same CSP + sandbox isolation as
 * HTML artifacts at zero extra code.
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
  // PDF in browser: an iframe pointing at the raw URL renders the browser's
  // native PDF viewer with the same isolation guarantees as HTML artifacts.
  'application/pdf': HtmlRenderer,
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
        className="w-8 border-l border-[var(--border-default)] writing-mode-vertical text-xs"
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
      <div className="flex items-center justify-between border-b border-[var(--border-default)]">
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
