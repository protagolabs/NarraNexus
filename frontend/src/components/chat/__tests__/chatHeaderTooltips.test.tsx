/**
 * Req #3: the header panel-entry icon buttons (Jobs / Inbox / Artifacts) must
 * expose a hover tooltip AND an accessible name. Before the change they carried
 * only a native `title` (no accessible name via aria), so getByLabelText was
 * empty. Wrapping them in the Radix Tooltip + adding aria-label makes the name
 * queryable — revert either and this goes red.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/stores', async () => ({
  // Real barrel underneath (the header reads the studio store from it); only
  // the two UI stores below are stubbed.
  ...(await vi.importActual<typeof import('@/stores')>('@/stores')),
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
  isStreaming: false,
  currentSteps: [],
  chatTab: 'conversation' as const,
  onChatTabChange: vi.fn(),
};

describe('chat header panel-entry tooltips (#3)', () => {
  it('Jobs / Inbox / Artifacts buttons have accessible labels', () => {
    render(
      <MemoryRouter>
        <ChatHeader {...baseProps} />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText('Jobs')).toBeInTheDocument();
    expect(screen.getByLabelText('Inbox')).toBeInTheDocument();
    expect(screen.getByLabelText('Artifacts')).toBeInTheDocument();
  });
});
