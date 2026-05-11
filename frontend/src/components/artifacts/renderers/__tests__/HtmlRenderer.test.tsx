/**
 * @file_name: HtmlRenderer.test.tsx
 * @description: Security contract tests for HtmlRenderer.
 *
 * These tests assert the iframe sandbox attribute invariants that prevent
 * agent-emitted HTML from escaping isolation. If a future change relaxes the
 * sandbox policy, this test will fail, surfacing the security regression.
 *
 * NOTE: No test runner (vitest/jest) is currently configured in package.json.
 * This file uses vitest-style imports as documentation-as-code. A future dev
 * who adds @testing-library/react + vitest will find these tests auto-discovered.
 */

import { describe, expect, test } from 'vitest';
import { render } from '@testing-library/react';
import HtmlRenderer from '../HtmlRenderer';
import type { Artifact } from '@/types/artifact';

const fakeArtifact: Artifact = {
  artifact_id: 'art_test1234',
  agent_id: 'agent_x',
  user_id: 'user_y',
  session_id: 's',
  title: 't',
  kind: 'text/html',
  description: null,
  pinned: false,
  latest_version: 1,
  created_at: '2026-05-08T00:00:00Z',
  updated_at: '2026-05-08T00:00:00Z',
};

describe('HtmlRenderer security', () => {
  test('sandbox attribute does not allow same-origin or top-navigation', () => {
    const { container } = render(<HtmlRenderer artifact={fakeArtifact} version={1} />);
    const iframe = container.querySelector('iframe')!;
    const sandbox = (iframe.getAttribute('sandbox') ?? '').split(/\s+/);
    expect(sandbox).toContain('allow-scripts');
    expect(sandbox).not.toContain('allow-same-origin');
    expect(sandbox).not.toContain('allow-top-navigation');
    expect(sandbox).not.toContain('allow-popups-to-escape-sandbox');
  });

  test('src points at raw URL with kind-specific path', () => {
    const { container } = render(<HtmlRenderer artifact={fakeArtifact} version={3} />);
    const iframe = container.querySelector('iframe')!;
    expect(iframe.getAttribute('src')).toBe('/api/agents/agent_x/artifacts/art_test1234/v3/raw');
  });
});
