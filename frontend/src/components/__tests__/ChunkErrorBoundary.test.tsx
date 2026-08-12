/**
 * @file_name: ChunkErrorBoundary.test.tsx
 * @description: The route-level boundary distinguishes a stale-chunk crash (a
 * deploy artifact — auto-recover once + "new version, refresh") from a real
 * render bug (no recovery — "something went wrong"), and never blank-white-
 * screens. The recover action is injected so BOTH branches are asserted (the
 * load-bearing `if (chunk)` — not just the rendered copy).
 */
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RELOAD_GUARD_KEY } from '@/lib/chunkReload';

import { ChunkErrorBoundary } from '../ChunkErrorBoundary';

function ChunkBoom(): never {
  throw new Error('Failed to fetch dynamically imported module: /assets/Foo-abc123.js');
}
function RealBug(): never {
  throw new Error("Cannot read properties of undefined (reading 'map')");
}

beforeEach(() => {
  // Clear the once-per-session guard so a chunk crash CAN trigger recovery.
  window.sessionStorage.removeItem(RELOAD_GUARD_KEY);
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
  test('a stale-chunk crash auto-recovers once AND shows the "new version" prompt', () => {
    const restore = silenceConsole();
    const recover = vi.fn();
    render(<ChunkErrorBoundary recover={recover}><ChunkBoom /></ChunkErrorBoundary>);
    expect(recover).toHaveBeenCalledTimes(1); // the load-bearing `if (chunk)` branch
    expect(screen.getByText(/new version/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /refresh/i })).toBeTruthy();
    restore();
  });

  test('a real render bug does NOT recover and shows a neutral error', () => {
    const restore = silenceConsole();
    const recover = vi.fn();
    render(<ChunkErrorBoundary recover={recover}><RealBug /></ChunkErrorBoundary>);
    expect(recover).not.toHaveBeenCalled(); // must NOT auto-reload a real bug (would loop)
    expect(screen.getByText(/something went wrong/i)).toBeTruthy();
    expect(screen.queryByText(/new version/i)).toBeNull();
    restore();
  });

  test('recovers at most once per session', () => {
    const restore = silenceConsole();
    const recover = vi.fn();
    render(<ChunkErrorBoundary recover={recover}><ChunkBoom /></ChunkErrorBoundary>);
    render(<ChunkErrorBoundary recover={recover}><ChunkBoom /></ChunkErrorBoundary>);
    expect(recover).toHaveBeenCalledTimes(1); // guard blocks the second
    restore();
  });

  test('renders children unchanged when nothing throws', () => {
    render(<ChunkErrorBoundary><span>all good</span></ChunkErrorBoundary>);
    expect(screen.getByText('all good')).toBeTruthy();
  });
});
