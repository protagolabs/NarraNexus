/**
 * @file_name: kindRegistry.test.ts
 * @description: The kind capability registry is the single source of truth
 * for what each ArtifactKind can do (render / edit / save / preview /
 * download). These tests pin the v1 capability matrix decided in the
 * 2026-08-19 editing spec — a wrong entry here silently enables editing on a
 * kind that cannot round-trip, or kills it on one that can.
 */

import { describe, expect, it } from 'vitest';
import type { ArtifactKind } from '@/types/artifact';
import { KIND_REGISTRY } from '../kindRegistry';

const ALL_KINDS: ArtifactKind[] = [
  'text/html',
  'application/vnd.echarts+json',
  'text/csv',
  'text/markdown',
  'image/png',
  'image/jpeg',
  'application/pdf',
  'application/vnd.officecli-live',
  'application/x-url',
];

describe('KIND_REGISTRY', () => {
  it('covers every ArtifactKind and nothing else', () => {
    expect(Object.keys(KIND_REGISTRY).sort()).toEqual([...ALL_KINDS].sort());
  });

  it('every kind has a renderer', () => {
    for (const kind of ALL_KINDS) {
      expect(KIND_REGISTRY[kind].renderer, kind).toBeDefined();
    }
  });

  it('pins the edit-surface matrix (no-mode framework, 2026-08-19)', () => {
    const surfaces = Object.fromEntries(
      ALL_KINDS.map((k) => [k, KIND_REGISTRY[k].editSurface]),
    );
    expect(surfaces).toEqual({
      'text/markdown': 'block-editor',
      'text/html': 'per-element',
      'text/csv': 'resident-editor',
      'application/vnd.echarts+json': 'none', // canvas: AI channel only
      'image/png': 'none',
      'image/jpeg': 'none',
      'application/pdf': 'none',
      'application/vnd.officecli-live': 'office-watch',
      'application/x-url': 'none',
    });
  });

  it('save mode exists exactly for editable surfaces', () => {
    expect(KIND_REGISTRY['text/markdown'].saveMode).toBe('debounced-autosave');
    expect(KIND_REGISTRY['text/html'].saveMode).toBe('element-commit');
    expect(KIND_REGISTRY['text/csv'].saveMode).toBe('explicit-dirty');
    expect(KIND_REGISTRY['application/vnd.officecli-live'].saveMode).toBe('office-resident');
    for (const kind of ALL_KINDS) {
      const d = KIND_REGISTRY[kind];
      if (d.editSurface === 'none') expect(d.saveMode, kind).toBeNull();
      else expect(d.saveMode, kind).not.toBeNull();
    }
  });

  it('keeps the download extensions the menu shipped with', () => {
    const ext = Object.fromEntries(
      ALL_KINDS.map((k) => [k, KIND_REGISTRY[k].downloadExt]),
    );
    expect(ext).toEqual({
      'text/html': 'html',
      'application/vnd.echarts+json': 'json',
      'text/csv': 'csv',
      'text/markdown': 'md',
      'image/png': 'png',
      'image/jpeg': 'jpg',
      'application/pdf': 'pdf',
      // No natural extension pre-registry; the menu fell back to 'bin'.
      'application/vnd.officecli-live': 'bin',
      'application/x-url': 'bin',
    });
  });

  it('chart image export is a chart-only affordance', () => {
    for (const kind of ALL_KINDS) {
      expect(Boolean(KIND_REGISTRY[kind].chartImageExport), kind).toBe(
        kind === 'application/vnd.echarts+json',
      );
    }
  });

  it('preview strategies match what ArtifactPreviewCard shipped with', () => {
    const previews = Object.fromEntries(
      ALL_KINDS.map((k) => [k, KIND_REGISTRY[k].preview]),
    );
    expect(previews).toEqual({
      'image/png': 'image',
      'image/jpeg': 'image',
      'text/csv': 'csv-head',
      'text/markdown': 'md-head',
      'text/html': 'placeholder',
      'application/vnd.echarts+json': 'placeholder',
      'application/pdf': 'placeholder',
      'application/vnd.officecli-live': 'none',
      'application/x-url': 'none',
    });
    // placeholder kinds must say which i18n line to show
    for (const kind of ALL_KINDS) {
      const d = KIND_REGISTRY[kind];
      if (d.preview === 'placeholder') {
        expect(d.previewPlaceholderKey, kind).toBeTruthy();
      } else {
        expect(d.previewPlaceholderKey, kind).toBeUndefined();
      }
    }
  });

  it('labels cover exactly the kinds the settings list used to label', () => {
    expect(KIND_REGISTRY['text/html'].label).toBe('HTML');
    expect(KIND_REGISTRY['application/vnd.echarts+json'].label).toBe('Chart');
    expect(KIND_REGISTRY['text/csv'].label).toBe('CSV');
    expect(KIND_REGISTRY['text/markdown'].label).toBe('Markdown');
    expect(KIND_REGISTRY['image/png'].label).toBe('PNG');
    expect(KIND_REGISTRY['image/jpeg'].label).toBe('JPEG');
    expect(KIND_REGISTRY['application/pdf'].label).toBe('PDF');
    // pre-registry fallback for these was the raw kind string — keep it
    expect(KIND_REGISTRY['application/vnd.officecli-live'].label).toBeUndefined();
    expect(KIND_REGISTRY['application/x-url'].label).toBeUndefined();
  });

  it('declares selection→AI mounts per the editability matrix (v1.5 hook)', () => {
    const sel = Object.fromEntries(
      ALL_KINDS.map((k) => [k, KIND_REGISTRY[k].selectionToAI]),
    );
    expect(sel).toEqual({
      'text/markdown': true,
      'text/html': true,
      'text/csv': true,
      'application/pdf': true, // PDF.js selection as quote anchor
      'application/vnd.officecli-live': true, // watch `selected` → agent
      'application/vnd.echarts+json': false, // canvas has no selectable text
      'image/png': false,
      'image/jpeg': false,
      'application/x-url': false,
    });
  });
  it('pins the PUT-editable kind set to the backend (review #334 I18)', () => {
    // Backend twin: tests/artifact/test_user_edit.py
    // ::test_editable_kinds_pinned_to_frontend_registry — both sides pin the
    // SAME literal set; a kind added on one side without the other shows as
    // an editor whose saves always 400.
    const putEditable = ALL_KINDS.filter((k) => {
      const surface = KIND_REGISTRY[k].editSurface;
      return surface !== 'none' && surface !== 'office-watch';
    });
    expect(putEditable.sort()).toEqual(['text/csv', 'text/html', 'text/markdown']);
  });
});
