/**
 * @file_name: ChunkErrorBoundary.test.tsx
 * @description: The route-level error boundary that turns a crashed render
 * (most often a stale lazy-chunk 404 after a deploy) into a "refresh" prompt
 * instead of a blank white screen.
 */
import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChunkErrorBoundary } from '../ChunkErrorBoundary';

function Boom(): JSX.Element {
  throw new Error('Failed to fetch dynamically imported module: /assets/Foo-abc123.js');
}

describe('ChunkErrorBoundary', () => {
  test('renders a refresh prompt instead of a blank screen when a child throws', () => {
    // React logs the caught error; silence it to keep the test output clean.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <ChunkErrorBoundary>
        <Boom />
      </ChunkErrorBoundary>,
    );
    // A visible recovery affordance must exist (not an empty DOM).
    expect(screen.getByRole('button', { name: /refresh|reload/i })).toBeTruthy();
    spy.mockRestore();
  });

  test('renders children unchanged when nothing throws', () => {
    render(
      <ChunkErrorBoundary>
        <span>all good</span>
      </ChunkErrorBoundary>,
    );
    expect(screen.getByText('all good')).toBeTruthy();
  });
});
