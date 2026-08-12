/**
 * @file_name: ChunkErrorBoundary.test.tsx
 * @description: The route-level boundary distinguishes a stale-chunk crash (a
 * deploy artifact — "new version, refresh") from a real render bug ("something
 * went wrong"), so a genuine bug is NOT masked as a version update, and never
 * blank-white-screens.
 */
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import { ChunkErrorBoundary } from '../ChunkErrorBoundary';

function ChunkBoom(): JSX.Element {
  throw new Error('Failed to fetch dynamically imported module: /assets/Foo-abc123.js');
}
function RealBug(): JSX.Element {
  throw new Error("Cannot read properties of undefined (reading 'map')");
}

beforeEach(() => {
  // Pre-arm the once-per-session guard so a chunk crash does NOT call
  // window.location.reload() (unimplemented in jsdom) during the test.
  window.sessionStorage.setItem('nx-chunk-reloaded', '1');
});

function silenceConsole() {
  const e = vi.spyOn(console, 'error').mockImplementation(() => {});
  const w = vi.spyOn(console, 'warn').mockImplementation(() => {});
  return () => {
    e.mockRestore();
    w.mockRestore();
  };
}

describe('ChunkErrorBoundary', () => {
  test('a stale-chunk crash shows the "new version" prompt', () => {
    const restore = silenceConsole();
    render(<ChunkErrorBoundary><ChunkBoom /></ChunkErrorBoundary>);
    expect(screen.getByText(/new version/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /refresh/i })).toBeTruthy();
    restore();
  });

  test('a real render bug shows a neutral error, NOT "new version"', () => {
    const restore = silenceConsole();
    render(<ChunkErrorBoundary><RealBug /></ChunkErrorBoundary>);
    expect(screen.getByText(/something went wrong/i)).toBeTruthy();
    expect(screen.queryByText(/new version/i)).toBeNull();
    expect(screen.getByRole('button', { name: /refresh/i })).toBeTruthy();
    restore();
  });

  test('renders children unchanged when nothing throws', () => {
    render(<ChunkErrorBoundary><span>all good</span></ChunkErrorBoundary>);
    expect(screen.getByText('all good')).toBeTruthy();
  });
});
