/**
 * Req #3: the header panel-entry icon buttons (Jobs / Inbox / Artifacts) must
 * expose a hover tooltip AND an accessible name. Before the change they carried
 * only a native `title` (no accessible name via aria), so getByLabelText was
 * empty. Wrapping them in the Radix Tooltip + adding aria-label makes the name
 * queryable — revert either and this goes red.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/stores', () => ({
  useConfigStore: (sel: (s: unknown) => unknown) =>
    sel({ agents: [{ agent_id: 'a1', name: 'Analyst' }], setAgentId: vi.fn() }),
  useChatStore: (sel: (s: unknown) => unknown) => sel({ setActiveAgent: vi.fn() }),
  useUIStore: (sel: (s: unknown) => unknown) =>
    sel({ sidebarCollapsed: false, setSidebarCollapsed: vi.fn(), requestPanel: vi.fn() }),
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
  sessionLabel: '',
  isStreaming: false,
  currentSteps: [],
  chatTab: 'conversation' as const,
  onChatTabChange: vi.fn(),
  onOpenAgentConfig: vi.fn(),
};

describe('chat header panel-entry tooltips (#3)', () => {
  it('Jobs / Inbox / Artifacts buttons have accessible labels', () => {
    render(<ChatHeader {...baseProps} />);
    expect(screen.getByLabelText('Jobs')).toBeInTheDocument();
    expect(screen.getByLabelText('Inbox')).toBeInTheDocument();
    expect(screen.getByLabelText('Artifacts')).toBeInTheDocument();
  });
});
