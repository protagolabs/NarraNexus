/**
 * @file_name: scrollContainment.test.tsx
 * @description: Scroll-ownership contract tests for artifact renderers.
 *
 * The artifact column's content box is overflow-hidden by design (the
 * drag-freeze mechanism in ArtifactColumn relies on clipping), so every
 * renderer must own its scrolling: a bounded-height (h-full) container
 * with overflow-auto. Without the height bound the renderer grows to its
 * content height, its own overflow-auto never overflows, and the parent
 * silently clips everything past the first screen (bug: cloud artifacts
 * could not be scrolled at all, 2026-07-13 report).
 *
 * ArtifactRenderer is shared between the column and the zoom modal, so
 * its wrapper must be h-full/w-full — absolute positioning would escape
 * the modal's scale-sizer layers and break zoom panning.
 */

import { describe, expect, test, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';

vi.mock('@/services/artifactsApi', () => ({
  artifactsApi: {
    getRawUrl: vi.fn(async () => '/api/public/artifacts/raw/FAKE_TOKEN/'),
  },
  fetchArtifactText: vi.fn(async (url: string) =>
    url.includes('csv') ? 'col_a,col_b\n1,2' : '# heading\n\nbody text',
  ),
  fetchArtifactBlobUrl: vi.fn(async () => 'blob:http://tauri.localhost/fake'),
}));

vi.mock('@/lib/tauri', async () => {
  const actual = await vi.importActual<typeof import('@/lib/tauri')>('@/lib/tauri');
  return {
    ...actual,
    isTauri: () => false,
    fetchArtifactViaTauri: vi.fn(async () => null),
  };
});

import MarkdownRenderer from '../MarkdownRenderer';
import CsvRenderer from '../CsvRenderer';
import ArtifactRenderer from '../../ArtifactRenderer';
import type { Artifact } from '@/types/artifact';

const baseArtifact: Artifact = {
  artifact_id: 'art_scroll01',
  agent_id: 'agent_x',
  user_id: 'user_y',
  session_id: 's',
  original_session_id: null,
  title: 't',
  kind: 'text/markdown',
  description: null,
  pinned: false,
  file_path: 'agent_x_user_y/report/report.md',
  size_bytes: 1024,
  created_at: '2026-05-08T00:00:00Z',
  updated_at: '2026-05-08T00:00:00Z',
};

const csvArtifact: Artifact = {
  ...baseArtifact,
  artifact_id: 'art_scroll02',
  kind: 'text/csv',
  file_path: 'agent_x_user_y/data/table.csv',
};

function expectScrollOwner(el: Element) {
  const cls = el.className;
  expect(cls).toContain('h-full');
  expect(cls).toContain('overflow-auto');
  expect(cls).toContain('overscroll-contain');
}

describe('renderer scroll ownership', () => {
  test('markdown content container is a bounded scroll container', async () => {
    const { container } = render(<MarkdownRenderer artifact={baseArtifact} />);
    const content = await waitFor(() => {
      const el = container.querySelector('.markdown-content');
      if (!el) throw new Error('markdown content not rendered yet');
      return el;
    });
    expectScrollOwner(content);
  });

  test('csv table container is a bounded scroll container', async () => {
    const { container } = render(<CsvRenderer artifact={csvArtifact} />);
    const table = await waitFor(() => {
      const el = container.querySelector('table');
      if (!el) throw new Error('csv table not rendered yet');
      return el;
    });
    const scroller = table.parentElement as Element;
    expectScrollOwner(scroller);
  });

  test('ArtifactRenderer wraps renderers in a bounded box without absolute positioning', async () => {
    const { container } = render(<ArtifactRenderer artifact={baseArtifact} />);
    await waitFor(() => {
      if (!container.querySelector('.markdown-content')) {
        throw new Error('lazy renderer not loaded yet');
      }
    });
    const wrapper = container.firstElementChild as Element;
    expect(wrapper.className).toContain('h-full');
    expect(wrapper.className).toContain('w-full');
    // Shared with the zoom modal: absolute would escape the scale-sizer.
    expect(wrapper.className).not.toContain('absolute');
  });
});
