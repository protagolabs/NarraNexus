/**
 * Tests for AgentGroupSection (collapse toggle, unread aggregation, and the
 * display-only agent row — no per-row action menu since 2026-08-27).
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { AgentGroupSection } from '../AgentGroupSection';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const wrapRouter = (ui: React.ReactNode) => (
  <MemoryRouter>{ui}</MemoryRouter>
);

// ---------------------------------------------------------------------------
// AgentGroupSection
// ---------------------------------------------------------------------------

describe('AgentGroupSection', () => {
  const defaultProps = {
    teamId: 't1',
    teamName: 'Trading Desk',
    teamColor: '#e56',
    agents: [
      { agent_id: 'a1', name: 'Analyst' },
      { agent_id: 'a2', name: 'Risk Officer' },
    ],
    agentId: null,
    collapsed: false,
    onToggleCollapse: vi.fn(),
    onSelectAgent: vi.fn(),
    getRowMeta: () => ({ preview: '', time: '', unread: 0 }),
    getIsStreaming: () => false,
    completedAgentIds: [] as string[],
    currentUserId: 'u1',
  };

  it('renders the team name in the section header', () => {
    render(wrapRouter(<AgentGroupSection {...defaultProps} />));
    expect(screen.getByText('Trading Desk')).toBeInTheDocument();
  });

  it('renders member count badge', () => {
    render(wrapRouter(<AgentGroupSection {...defaultProps} />));
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('renders agent names when not collapsed', () => {
    render(wrapRouter(<AgentGroupSection {...defaultProps} />));
    expect(screen.getByText('Analyst')).toBeInTheDocument();
    expect(screen.getByText('Risk Officer')).toBeInTheDocument();
  });

  it('hides agent rows when collapsed=true', () => {
    render(
      wrapRouter(
        <AgentGroupSection {...defaultProps} collapsed={true} />
      )
    );
    expect(screen.queryByText('Analyst')).not.toBeInTheDocument();
    expect(screen.queryByText('Risk Officer')).not.toBeInTheDocument();
  });

  it('calls onToggleCollapse when header is clicked', () => {
    const onToggle = vi.fn();
    render(
      wrapRouter(
        <AgentGroupSection {...defaultProps} onToggleCollapse={onToggle} />
      )
    );
    // The header button with the triangle
    const headerBtn = screen.getByRole('button', { name: /trading desk/i });
    fireEvent.click(headerBtn);
    expect(onToggle).toHaveBeenCalledWith('t1');
  });

  it('shows aggregated unread pill in header when collapsed and unread > 0', () => {
    const getRowMeta = (aid: string) => ({
      preview: '',
      time: '',
      unread: aid === 'a1' ? 3 : 0,
    });
    render(
      wrapRouter(
        <AgentGroupSection
          {...defaultProps}
          collapsed={true}
          getRowMeta={getRowMeta}
        />
      )
    );
    // The aggregated count should be visible in the header.
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('does NOT show aggregated unread when count is 0', () => {
    render(
      wrapRouter(
        <AgentGroupSection {...defaultProps} collapsed={true} />
      )
    );
    // No unread pill — only the member count "2" should be present.
    // The digit "2" is the member count, not an unread count.
    // We're just verifying there is no separate unread pill showing 0.
    // The member count "2" is acceptable; there should be no "0" pill.
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('renders the Ungrouped section with hollow dot visual hint', () => {
    render(
      wrapRouter(
        <AgentGroupSection
          {...defaultProps}
          teamId={null}
          teamName="Ungrouped"
          teamColor={null}
        />
      )
    );
    // The Ungrouped section header should be present.
    expect(screen.getByText('Ungrouped')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Row affordances (2026-08-27 — kebab removed)
// ---------------------------------------------------------------------------

describe('agent row is display-only', () => {
  const props = {
    teamId: null,
    teamName: '',
    teamColor: null,
    agents: [{ agent_id: 'a1', name: 'Analyst', created_by: 'u1' }],
    agentId: null,
    collapsed: false,
    hideHeader: true,
    currentUserId: 'u1',
    onToggleCollapse: vi.fn(),
    onSelectAgent: vi.fn(),
    getRowMeta: () => ({ preview: '', time: '', unread: 0 }),
    getIsStreaming: () => false,
    completedAgentIds: [] as string[],
  };

  it('renders no per-row action menu — agent actions live on the profile page', () => {
    render(wrapRouter(<AgentGroupSection {...props} />));
    expect(screen.queryByLabelText(/agent options/i)).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('still selects the agent when the row is clicked', () => {
    const onSelectAgent = vi.fn();
    render(wrapRouter(<AgentGroupSection {...props} onSelectAgent={onSelectAgent} />));
    fireEvent.click(screen.getByText('Analyst'));
    expect(onSelectAgent).toHaveBeenCalledWith('a1');
  });
});
