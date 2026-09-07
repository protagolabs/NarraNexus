/**
 * Clicking the agent's avatar/name in the chat header opens that agent's
 * PROFILE page — it is a navigation link, not a menu. The agent-switcher
 * dropdown that used to live here was retired (2026-08-27): switching agents
 * belongs to the sidebar, and the ⋯ menu keeps its own door to the panels.
 *
 * `state.from === 'chat'` is load-bearing: it is what makes the profile's
 * breadcrumb offer "back to Chat" instead of "back to Agents".
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
vi.mock('@/stores', async () => ({
  // Real barrel underneath (the header reads the studio store from it); only
  // the two UI stores below are stubbed.
  ...(await vi.importActual<typeof import('@/stores')>('@/stores')),
  useUIStore: (sel: (s: unknown) => unknown) =>
    sel({
      sidebarCollapsed: false,
      setSidebarCollapsed: vi.fn(),
      requestPanel: vi.fn(),
    }),
  useArtifactStore: (sel: (s: unknown) => unknown) => sel({ artifacts: [] }),
}));
vi.mock('@/stores/bookmarkStore', () => ({
  useBookmarkStore: (sel: (s: unknown) => unknown) => sel({ agents: {} }),
}));
vi.mock('@/components/cost/CostPopover', () => ({ CostPopover: () => null }));
vi.mock('../ExecutionPopover', () => ({ ExecutionPopover: () => null }));

import { ChatHeader } from '../ChatHeader';

const baseProps = {
  agentId: 'a1',
  agentName: 'Analyst',
  isStreaming: false,
  currentSteps: [],
  chatTab: 'conversation' as const,
  onChatTabChange: vi.fn(),
};

function renderHeader(props: Partial<typeof baseProps> = {}) {
  return render(
    <MemoryRouter>
      <ChatHeader {...baseProps} {...props} />
    </MemoryRouter>,
  );
}

describe('chat header identity block', () => {
  beforeEach(() => navigate.mockClear());

  it('navigates to the agent profile, tagged as coming from chat', () => {
    renderHeader();
    fireEvent.click(screen.getByRole('button', { name: /view agent profile/i }));
    expect(navigate).toHaveBeenCalledWith('/app/agents/a1', { state: { from: 'chat' } });
  });

  it('encodes the agent id so an id with a slash still resolves', () => {
    renderHeader({ agentId: 'team/a1' });
    fireEvent.click(screen.getByRole('button', { name: /view agent profile/i }));
    expect(navigate).toHaveBeenCalledWith('/app/agents/team%2Fa1', { state: { from: 'chat' } });
  });

  it('does nothing without an agent — no navigation to /app/agents/', () => {
    renderHeader({ agentId: null });
    fireEvent.click(screen.getByRole('button', { name: /view agent profile/i }));
    expect(navigate).not.toHaveBeenCalled();
  });
});
