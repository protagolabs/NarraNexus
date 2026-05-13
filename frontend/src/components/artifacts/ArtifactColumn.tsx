/**
 * @file_name: ArtifactColumn.tsx
 * @description: The 4th column in the agent layout. Hosts ArtifactTabStrip plus a
 * lazy-rendered content area that dispatches to the appropriate renderer by
 * artifact kind. Collapses to a sliver button when the user dismisses it;
 * also renders as a sliver (never fully hidden) when no artifacts exist yet,
 * so the user always knows the panel is there. Auto-expands the moment a
 * new artifact arrives.
 *
 * Renderer dispatch uses React.lazy so each renderer bundle is only downloaded
 * when the corresponding kind is first activated — avoids pulling ECharts or
 * PDF viewer code on first load.
 *
 * PDF artifacts use a dedicated PdfRenderer based on <object> rather than an
 * iframe sandbox, because PDF.js in Firefox requires same-origin XHR and the
 * sandboxed iframe pattern breaks it. See PdfRenderer.tsx for the full rationale.
 */

import { lazy, Suspense, useEffect, useRef } from 'react';
import { ChevronLeft } from 'lucide-react';
import { useArtifactStore } from '@/stores';
import type { Artifact, ArtifactKind } from '@/types/artifact';
import ArtifactTabStrip from './ArtifactTabStrip';
import ArtifactDownloadMenu from './ArtifactDownloadMenu';

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
  // All hooks must run in the same order on every render — no conditional hook
  // calls. Selectors first, then early returns.
  const artifacts = useArtifactStore((s) => s.artifacts);
  const activeId = useArtifactStore((s) => s.activeArtifactId);
  const collapsed = useArtifactStore((s) => s.collapsed);
  const setCollapsed = useArtifactStore((s) => s.setCollapsed);
  const minimizedTabIds = useArtifactStore((s) => s.minimizedTabIds);
  const restoreTab = useArtifactStore((s) => s.restoreTab);

  // Auto-expand on artifact arrival.
  //
  // Pre-2026-05-13 behaviour: the column was unmounted entirely while
  // `artifacts.length === 0`, then suddenly popped into existence when
  // the first artifact landed — felt abrupt and a beat slow because the
  // user wasn't aware the panel existed. New behaviour: always render
  // the sliver (even with 0 artifacts) so the panel has a visual
  // presence from day one, and auto-uncollapse it the moment a new
  // artifact arrives so the user doesn't have to click the sliver to
  // discover the freshly-created artifact.
  //
  // We track the previous length in a ref. Mount initialises prev =
  // current, so first paint with cached artifacts (stale-while-
  // revalidate after agent switch) does NOT auto-expand — only genuine
  // growth past the previous tick triggers expansion. The "fights the
  // user who just collapsed" edge case is accepted as a tradeoff —
  // user explicitly asked for new-artifact-pops-it-open semantics
  // (they can re-collapse, and the next growth event will pop it open
  // again, which matches "tell me when something new is here").
  const prevLengthRef = useRef(artifacts.length);
  useEffect(() => {
    if (artifacts.length > prevLengthRef.current && collapsed) {
      setCollapsed(false);
    }
    prevLengthRef.current = artifacts.length;
  }, [artifacts.length, collapsed, setCollapsed]);

  // Sliver form: shown when the user collapsed the column OR when no
  // artifacts exist yet. The empty-state sliver advertises the panel's
  // existence so users know where artifacts will appear once the agent
  // creates one.
  const effectiveCollapsed = collapsed || artifacts.length === 0;
  if (effectiveCollapsed) {
    const hasArtifacts = artifacts.length > 0;
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="w-9 border border-[var(--border-default)] bg-[var(--bg-primary)] hover:bg-[var(--bg-secondary)] flex flex-col items-center pt-3 pb-2 group transition-colors"
        title={
          hasArtifacts
            ? `Click to expand · ${artifacts.length} artifact${artifacts.length === 1 ? '' : 's'}`
            : 'Artifacts will appear here once the agent creates one'
        }
        aria-label={
          hasArtifacts
            ? `Expand artifacts panel (${artifacts.length} items)`
            : 'Artifacts panel (empty)'
        }
      >
        {/* Top: vertical title so the user knows what this column is */}
        <span className="text-[11px] font-semibold [writing-mode:vertical-rl] tracking-wider whitespace-nowrap">
          {hasArtifacts ? `Artifacts (${artifacts.length})` : 'Artifacts'}
        </span>
        {/* Spacer to push the chevron to the bottom */}
        <span className="flex-1" />
        {/* Bottom: chevron pointing left to suggest "open out toward the chat" */}
        <ChevronLeft className="w-4 h-4 opacity-50 group-hover:opacity-100 transition-opacity" aria-hidden />
      </button>
    );
  }

  const minimized = artifacts.filter((a) => minimizedTabIds.has(a.artifact_id));

  const visibleArtifacts = artifacts.filter((a) => !minimizedTabIds.has(a.artifact_id));
  const effectiveActiveId =
    activeId && visibleArtifacts.some((a) => a.artifact_id === activeId)
      ? activeId
      : visibleArtifacts[0]?.artifact_id ?? null;
  const active = visibleArtifacts.find((a) => a.artifact_id === effectiveActiveId);
  const Renderer = active ? RENDERER_BY_KIND[active.kind] : null;

  return (
    <aside className="flex flex-col min-w-[320px] flex-[2] border border-[var(--border-default)] bg-[var(--bg-primary)] overflow-hidden">
      {/* Minimized strip — only renders when something is minimized.
          Click a chip to restore the tab. */}
      {minimized.length > 0 && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--border-default)] bg-[var(--bg-secondary)] text-xs overflow-x-auto">
          <span className="opacity-60 whitespace-nowrap">⊟ Minimized:</span>
          <div className="flex gap-1.5 flex-1 min-w-0">
            {minimized.map((a) => (
              <button
                key={a.artifact_id}
                onClick={() => restoreTab(a.artifact_id)}
                className="px-2 py-0.5 border border-[var(--border-default)] bg-[var(--bg-primary)] hover:bg-[var(--bg-tertiary)] truncate max-w-[14rem] text-left"
                title={`Restore "${a.title}"`}
              >
                ↺ {a.title}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Header row: tab strip on the left, action buttons on the right.
          Shares its bottom border with the tab strip's own border-b. */}
      <div className="flex items-center justify-between min-w-0">
        <div className="flex-1 min-w-0">
          <ArtifactTabStrip agentId={agentId} />
        </div>
        <div className="flex items-center gap-1 px-1 border-b border-[var(--border-default)] self-stretch">
          {active && <ArtifactDownloadMenu artifact={active} />}
          <button
            onClick={() => setCollapsed(true)}
            className="text-xs opacity-60 hover:opacity-100 px-2"
            title="Collapse panel"
            aria-label="Collapse artifacts panel"
          >
            ▶
          </button>
        </div>
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
