/**
 * @file_name: kindRegistry.ts
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: Single source of truth for what each ArtifactKind can do.
 *
 * Before this file, kind knowledge was scattered: ArtifactRenderer held the
 * renderer map, ArtifactPreviewCard a chain of `kind ===` branches,
 * ArtifactDownloadMenu its own ext map + `isChart`, ArtifactsSection a label
 * map. Adding a kind (or a capability like editing) meant hunting all of
 * them. Now every consumer looks up one descriptor, and a new kind is one
 * entry + one renderer file.
 *
 * `editSurface` / `saveMode` / `selectionToAI` encode the 2026-08-19 editing
 * design (no-mode framework): the tab shell mounts editing behaviour
 * declaratively from these fields. `office-watch` and `selectionToAI` are
 * declared here but consumed by later commits (office editing spec / the
 * selection→AI channel) — declaring them now means those commits don't have
 * to touch the shape of this table.
 */

import { lazy } from 'react';
import type { Artifact, ArtifactKind } from '@/types/artifact';

const HtmlRenderer = lazy(() => import('./renderers/HtmlRenderer'));
const ChartRenderer = lazy(() => import('./renderers/ChartRenderer'));
const CsvRenderer = lazy(() => import('./renderers/CsvRenderer'));
const ImageRenderer = lazy(() => import('./renderers/ImageRenderer'));
const MarkdownRenderer = lazy(() => import('./renderers/MarkdownRenderer'));
const PdfRenderer = lazy(() => import('./renderers/PdfRenderer'));
const OfficeWatchViewer = lazy(() => import('./OfficeWatchViewer'));
const UrlRenderer = lazy(() => import('./renderers/UrlRenderer'));

export type RendererComponent = React.LazyExoticComponent<
  React.ComponentType<{ artifact: Artifact }>
>;

/**
 * Which editing surface the tab offers. There is deliberately NO view/edit
 * mode toggle anywhere — the render surface itself takes a cursor where the
 * kind allows it (no-mode framework):
 *  - block-editor:    the render IS a WYSIWYG editor (markdown).
 *  - per-element:     rendered page, but clicking into static text makes that
 *                     one element editable; blur commits (html).
 *  - resident-editor: the source editor IS the render, always editable (csv).
 *  - office-watch:    live officecli watch page; edits go through officecli
 *                     command translation (office editing spec).
 *  - none:            read-only surface; user changes go through the AI.
 */
export type EditSurface =
  | 'block-editor'
  | 'per-element'
  | 'resident-editor'
  | 'office-watch'
  | 'none';

/**
 * How user edits reach disk. Null exactly when editSurface is 'none'.
 *  - debounced-autosave: continuous typing, saved on blur + idle (markdown).
 *  - element-commit:     each element blur is one atomic commit (html).
 *  - explicit-dirty:     Cmd+S / save button with dirty guards (csv).
 *  - office-resident:    officecli resident serializes all writers.
 */
export type SaveMode =
  | 'debounced-autosave'
  | 'element-commit'
  | 'explicit-dirty'
  | 'office-resident'
  | null;

/** What ArtifactPreviewCard shows inside a chat message. */
export type PreviewStrategy = 'image' | 'csv-head' | 'md-head' | 'placeholder' | 'none';

export interface KindDescriptor {
  renderer: RendererComponent;
  editSurface: EditSurface;
  saveMode: SaveMode;
  /** v1.5 mount point: can a selection on this surface be sent to the AI? */
  selectionToAI: boolean;
  preview: PreviewStrategy;
  /** i18n key for the preview line; present iff preview === 'placeholder'. */
  previewPlaceholderKey?: string;
  /**
   * Download filename extension. Absent when the kind has no SINGLE natural
   * extension (office-live covers pptx/docx/xlsx) — consumers must go
   * through downloadExtFor, which derives it from the artifact's file_path.
   * A static 'bin' here was the Shenzhen-r2 ".bin download" bug.
   */
  downloadExt?: string;
  /** Human label for admin lists; consumers fall back to the raw kind. */
  label?: string;
  /** Chart-only: PNG/JPEG export entries in the download menu. */
  chartImageExport?: boolean;
}

export const KIND_REGISTRY: Record<ArtifactKind, KindDescriptor> = {
  'text/html': {
    renderer: HtmlRenderer,
    editSurface: 'per-element',
    saveMode: 'element-commit',
    selectionToAI: true,
    preview: 'placeholder',
    previewPlaceholderKey: 'artifacts.preview.html',
    downloadExt: 'html',
    label: 'HTML',
  },
  'application/vnd.echarts+json': {
    renderer: ChartRenderer,
    // Canvas render: no selectable text, and the source-editor escape hatch
    // died with the mode toggle — chart changes are the AI's job.
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'placeholder',
    previewPlaceholderKey: 'artifacts.preview.chart',
    downloadExt: 'json',
    label: 'Chart',
    chartImageExport: true,
  },
  'text/csv': {
    renderer: CsvRenderer,
    editSurface: 'resident-editor',
    saveMode: 'explicit-dirty',
    selectionToAI: true,
    preview: 'csv-head',
    downloadExt: 'csv',
    label: 'CSV',
  },
  'text/markdown': {
    renderer: MarkdownRenderer,
    editSurface: 'block-editor',
    saveMode: 'debounced-autosave',
    selectionToAI: true,
    preview: 'md-head',
    downloadExt: 'md',
    label: 'Markdown',
  },
  'image/png': {
    renderer: ImageRenderer,
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'image',
    downloadExt: 'png',
    label: 'PNG',
  },
  'image/jpeg': {
    renderer: ImageRenderer,
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'image',
    downloadExt: 'jpg',
    label: 'JPEG',
  },
  'application/pdf': {
    renderer: PdfRenderer,
    // PDF is always a downstream product here — editing the projection would
    // fork it from its source. PDF.js text selection still works as a quote
    // anchor for the AI channel.
    editSurface: 'none',
    saveMode: null,
    selectionToAI: true,
    preview: 'placeholder',
    previewPlaceholderKey: 'artifacts.preview.pdf',
    downloadExt: 'pdf',
    label: 'PDF',
  },
  'application/vnd.officecli-live': {
    renderer: OfficeWatchViewer,
    editSurface: 'office-watch',
    saveMode: 'office-resident',
    selectionToAI: true,
    preview: 'none',
  },
  'application/x-url': {
    renderer: UrlRenderer,
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'none',
  },
};

/**
 * The download extension for one artifact: the registry's static extension
 * when the kind has one, else the file_path's own extension (sanitized —
 * it feeds a filename), else 'bin'. This is the fix for the Shenzhen-r2
 * ".bin download" bug: an office artifact's real extension (pptx/docx/xlsx)
 * lives only in its pointer, and the kind is an internal render marker,
 * not a transport type.
 */
export function downloadExtFor(artifact: Pick<Artifact, 'kind' | 'file_path'>): string {
  // `?.` stays despite the exhaustive Record type: `kind` is a server value
  // and a NEWER backend may ship a kind this build's union doesn't know.
  const staticExt = KIND_REGISTRY[artifact.kind]?.downloadExt;
  if (staticExt) return staticExt;
  const base = (artifact.file_path ?? '').split('/').pop() ?? '';
  const dot = base.lastIndexOf('.');
  const ext = dot > 0 ? base.slice(dot + 1) : '';
  return /^[A-Za-z0-9]{1,16}$/.test(ext) ? ext.toLowerCase() : 'bin';
}
