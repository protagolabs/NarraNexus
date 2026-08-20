/**
 * Clicking the agent's NAME in the chat header answers "talk to someone
 * else" — an agent switcher listing every agent — never the settings menu.
 * Settings keep their own door (the ⋯ menu on the right).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const setAgentId = vi.fn();
const setActiveAgent = vi.fn();

vi.mock('@/stores', () => ({
  useConfigStore: (sel: (s: unknown) => unknown) =>
    sel({
      agents: [
        { agent_id: 'a1', name: 'Analyst' },
        { agent_id: 'a2', name: 'Writer' },
      ],
      setAgentId,
    }),
  useChatStore: (sel: (s: unknown) => unknown) => sel({ setActiveAgent }),
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
  markTabOpened: vi.fn(),
  deriveTabStatus: () => ({ kind: 'none' }),
}));
vi.mock('@/components/cost/CostPopover', () => ({ CostPopover: () => null }));
vi.mock('../ExecutionPopover', () => ({ ExecutionPopover: () => null }));

import { ChatHeader } from '../ChatHeader';

const baseProps = {
  agentId: 'a1',
  agentName: 'Analyst',
  sessionLabel: '',
  isStreaming: false,
  currentSteps: [],
  chatTab: 'chat' as const,
  onChatTabChange: vi.fn(),
  onOpenAgentConfig: vi.fn(),
};

describe('chat header agent switcher', () => {
  beforeEach(() => {
    setAgentId.mockClear();
    setActiveAgent.mockClear();
  });

  it('name click lists agents; picking one switches to it', () => {
    render(<ChatHeader {...baseProps} />);
    fireEvent.click(screen.getByRole('button', { name: /switch agent/i }));
    fireEvent.click(screen.getByRole('button', { name: /writer/i }));
    expect(setAgentId).toHaveBeenCalledWith('a2');
    expect(setActiveAgent).toHaveBeenCalledWith('a2');
  });

  it('picking the current agent closes without a switch', () => {
    render(<ChatHeader {...baseProps} />);
    fireEvent.click(screen.getByRole('button', { name: /switch agent/i }));
    const rows = screen.getAllByRole('button', { name: /analyst/i });
    fireEvent.click(rows[rows.length - 1]);
    expect(setAgentId).not.toHaveBeenCalled();
  });
});
