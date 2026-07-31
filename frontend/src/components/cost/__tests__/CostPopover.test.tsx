/**
 * @file_name: CostPopover.test.tsx
 * @date: 2026-07-15
 * @description: Regression tests for provider-neutral token usage labels.
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { CostPopover } from '../CostPopover';

// Cache-heavy shape on purpose: the live regression was an agent whose
// input side was >99% cache tokens, which the popover used to drop entirely
// (showing "input 213" for a 1.2M-token week and making the helper row look
// bigger than the main loop).
vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agentId: 'agent_test' }),
  usePreloadStore: () => ({
    costSummary: {
      total_cost_usd: 0,
      total_input_tokens: 100,
      total_output_tokens: 50,
      total_cache_read_tokens: 820,
      total_cache_creation_tokens: 110,
      by_model: {
        '__main_model__': {
          cost: 0,
          input_tokens: 80,
          output_tokens: 40,
          cache_read_tokens: 800,
          cache_creation_tokens: 100,
          call_count: 1,
        },
        '__helper_model__': {
          cost: 0,
          input_tokens: 20,
          output_tokens: 10,
          cache_read_tokens: 20,
          cache_creation_tokens: 10,
          call_count: 1,
        },
      },
      daily: [],
    },
    costLoading: false,
    refreshCost: vi.fn(),
  }),
  useChatStore: (
    selector: (state: { isStreaming: boolean }) => unknown,
  ) => selector({ isStreaming: false }),
}));

vi.mock('@/lib/api', () => ({
  api: {
    getCosts: vi.fn(),
  },
}));

describe('CostPopover', () => {
  it('shows provider-neutral labels for main and helper usage', () => {
    render(<CostPopover />);

    fireEvent.click(
      screen.getByTitle('Token usage — click for details'),
    );

    expect(screen.getByText('Model usage')).toBeInTheDocument();
    expect(screen.getByText('Helper Model Usage')).toBeInTheDocument();
    expect(screen.queryByText('Claude Code')).not.toBeInTheDocument();
    expect(screen.queryByText('__main_model__')).not.toBeInTheDocument();
    expect(screen.queryByText('__helper_model__')).not.toBeInTheDocument();
  });

  it('counts cache buckets into every displayed total', () => {
    render(<CostPopover />);

    fireEvent.click(
      screen.getByTitle('Token usage — click for details'),
    );

    // Grand total = 100 in + 820 cache read + 110 cache write + 50 out.
    expect(screen.getByText('1.1k')).toBeInTheDocument();
    // in/out line: input side includes both cache buckets (1030 → "1.0k").
    expect(screen.getByText('1.0k in / 50 out')).toBeInTheDocument();
    // Cache detail line is visible when cache activity exists.
    expect(
      screen.getByText('incl. cache read 820 · write 110'),
    ).toBeInTheDocument();
    // Per-model rows: main 80+40+800+100=1020 ("1.0k"), helper 20+10+20+10=60.
    // Without the cache buckets the helper row (30) would outrank main (120)
    // in the old sort — the live regression this guards against.
    expect(screen.getByText('60')).toBeInTheDocument();
    const rows = screen.getAllByText(/^1\.0k$/);
    expect(rows.length).toBeGreaterThanOrEqual(1);
  });
});
