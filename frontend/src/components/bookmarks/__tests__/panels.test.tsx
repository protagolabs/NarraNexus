/**
 * @file_name: panels.test.tsx
 * @date: 2026-06-10
 * @description: Tests for ActivityPanel and AgentProfilePanel orchestration.
 *
 * Strategy: mock heavy child panels so tests focus on the orchestration
 * layer — section ordering, focusKey → markOpened, accordion single-open
 * behavior, default-open follows profile highlight.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { useBookmarkStore } from '@/stores/bookmarkStore';

// ---------------------------------------------------------------------------
// Mocks — lightweight stubs for all heavy child panels
// ---------------------------------------------------------------------------

vi.mock('@/components/jobs/JobsPanel', () => ({
  JobsPanel: () => <div data-testid="jobs-panel" />,
}));

vi.mock('@/components/inbox/AgentInboxPanel', () => ({
  AgentInboxPanel: () => <div data-testid="inbox-panel" />,
}));

vi.mock('@/components/awareness/AwarenessPanel', () => ({
  AwarenessPanel: () => <div data-testid="awareness-panel" />,
}));

vi.mock('@/components/skills/SkillsPanel', () => ({
  SkillsPanel: () => <div data-testid="skills-panel" />,
}));

vi.mock('@/components/runtime/NarrativeList', () => ({
  NarrativeList: () => <div data-testid="narrative-list">NarrativeList</div>,
}));

// Import components under test AFTER mocks
import { ActivityPanel } from '../ActivityPanel';
import { AgentProfilePanel } from '../AgentProfilePanel';

// ---------------------------------------------------------------------------
// ActivityPanel
// ---------------------------------------------------------------------------

describe('ActivityPanel – section rendering', () => {
  it('renders the Jobs section label', () => {
    render(<ActivityPanel agentId="agent1" />);
    expect(screen.getByText(/jobs/i)).toBeInTheDocument();
  });

  it('renders the Inbox section label', () => {
    render(<ActivityPanel agentId="agent1" />);
    expect(screen.getByText(/inbox/i)).toBeInTheDocument();
  });

  it('renders JobsPanel child', () => {
    render(<ActivityPanel agentId="agent1" />);
    expect(screen.getByTestId('jobs-panel')).toBeInTheDocument();
  });

  it('renders AgentInboxPanel child', () => {
    render(<ActivityPanel agentId="agent1" />);
    expect(screen.getByTestId('inbox-panel')).toBeInTheDocument();
  });

  it('Jobs section appears before Inbox section in DOM order', () => {
    render(<ActivityPanel agentId="agent1" />);
    const jobs = screen.getByTestId('jobs-panel');
    const inbox = screen.getByTestId('inbox-panel');
    // compareDocumentPosition: 4 = following (jobs comes before inbox)
    expect(jobs.compareDocumentPosition(inbox) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('ActivityPanel – focusKey job:* triggers markOpened', () => {
  beforeEach(() => {
    act(() => {
      useBookmarkStore.getState().clearAll();
    });
  });

  it('calls markOpened when focusKey is job:<id> on mount', () => {
    act(() => {
      useBookmarkStore.getState().noteJobCompleted('agent1', 'job42', 'Morning Brief');
    });

    const spy = vi.spyOn(useBookmarkStore.getState(), 'markOpened');
    render(<ActivityPanel agentId="agent1" focusKey="job:job42" />);
    expect(spy).toHaveBeenCalledWith('agent1', 'job:job42');
    spy.mockRestore();
  });

  it('calls markOpened when focusKey is "inbox" on mount', () => {
    act(() => {
      useBookmarkStore.getState().noteInboxUnread('agent1', 3);
    });

    const spy = vi.spyOn(useBookmarkStore.getState(), 'markOpened');
    render(<ActivityPanel agentId="agent1" focusKey="inbox" />);
    expect(spy).toHaveBeenCalledWith('agent1', 'inbox');
    spy.mockRestore();
  });

  it('does not call markOpened when no focusKey', () => {
    const spy = vi.spyOn(useBookmarkStore.getState(), 'markOpened');
    render(<ActivityPanel agentId="agent1" />);
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// AgentProfilePanel – accordion single-open behavior
// ---------------------------------------------------------------------------

describe('AgentProfilePanel – accordion renders all three sections', () => {
  it('renders Awareness section trigger', () => {
    render(<AgentProfilePanel agentId="agent1" />);
    expect(screen.getByRole('button', { name: /awareness/i })).toBeInTheDocument();
  });

  it('renders Skills & MCP section trigger', () => {
    render(<AgentProfilePanel agentId="agent1" />);
    expect(screen.getByRole('button', { name: /skills/i })).toBeInTheDocument();
  });

  it('renders Memory section trigger', () => {
    render(<AgentProfilePanel agentId="agent1" />);
    expect(screen.getByRole('button', { name: /memory/i })).toBeInTheDocument();
  });
});

describe('AgentProfilePanel – default-open behavior', () => {
  beforeEach(() => {
    act(() => {
      useBookmarkStore.getState().clearAll();
    });
  });

  it('opens Awareness section by default (no highlight)', () => {
    render(<AgentProfilePanel agentId="agent1" />);
    expect(screen.getByTestId('awareness-panel')).toBeInTheDocument();
    // The other two panels should NOT be in DOM (closed)
    expect(screen.queryByTestId('skills-panel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('narrative-list')).not.toBeInTheDocument();
  });

  it('opens the highlighted section by default when profile:skills highlight exists', () => {
    act(() => {
      useBookmarkStore.getState().noteProfileUpdate('agent1', 'skills');
    });
    render(<AgentProfilePanel agentId="agent1" />);
    // skills section should be open, not awareness
    expect(screen.getByTestId('skills-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('awareness-panel')).not.toBeInTheDocument();
  });

  it('opens the highlighted section by default when profile:memory highlight exists', () => {
    act(() => {
      useBookmarkStore.getState().noteProfileUpdate('agent1', 'memory');
    });
    render(<AgentProfilePanel agentId="agent1" />);
    expect(screen.getByTestId('narrative-list')).toBeInTheDocument();
    expect(screen.queryByTestId('awareness-panel')).not.toBeInTheDocument();
  });
});

describe('AgentProfilePanel – single-open accordion', () => {
  beforeEach(() => {
    act(() => {
      useBookmarkStore.getState().clearAll();
    });
  });

  it('only one section is open at a time — opening skills closes awareness', () => {
    render(<AgentProfilePanel agentId="agent1" />);

    // Awareness is open by default
    expect(screen.getByTestId('awareness-panel')).toBeInTheDocument();

    // Click the Skills section trigger
    const skillsBtn = screen.getByRole('button', { name: /skills/i });
    fireEvent.click(skillsBtn);

    // Now skills should be open, awareness closed
    expect(screen.getByTestId('skills-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('awareness-panel')).not.toBeInTheDocument();
  });

  it('only one section is open at a time — opening memory closes skills', () => {
    render(<AgentProfilePanel agentId="agent1" />);

    // Open skills first
    fireEvent.click(screen.getByRole('button', { name: /skills/i }));
    expect(screen.getByTestId('skills-panel')).toBeInTheDocument();

    // Open memory
    fireEvent.click(screen.getByRole('button', { name: /memory/i }));
    expect(screen.getByTestId('narrative-list')).toBeInTheDocument();
    expect(screen.queryByTestId('skills-panel')).not.toBeInTheDocument();
  });
});

describe('AgentProfilePanel – focusKey profile:* opens correct section', () => {
  beforeEach(() => {
    act(() => {
      useBookmarkStore.getState().clearAll();
    });
  });

  it('focusKey profile:awareness opens awareness section', () => {
    render(<AgentProfilePanel agentId="agent1" focusKey="profile:awareness" />);
    expect(screen.getByTestId('awareness-panel')).toBeInTheDocument();
  });

  it('focusKey profile:skills opens skills section', () => {
    render(<AgentProfilePanel agentId="agent1" focusKey="profile:skills" />);
    expect(screen.getByTestId('skills-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('awareness-panel')).not.toBeInTheDocument();
  });

  it('focusKey profile:memory opens memory section', () => {
    render(<AgentProfilePanel agentId="agent1" focusKey="profile:memory" />);
    expect(screen.getByTestId('narrative-list')).toBeInTheDocument();
    expect(screen.queryByTestId('awareness-panel')).not.toBeInTheDocument();
  });

  it('focusKey profile:skills triggers markOpened on mount', () => {
    act(() => {
      useBookmarkStore.getState().noteProfileUpdate('agent1', 'skills');
    });

    const spy = vi.spyOn(useBookmarkStore.getState(), 'markOpened');
    render(<AgentProfilePanel agentId="agent1" focusKey="profile:skills" />);
    expect(spy).toHaveBeenCalledWith('agent1', 'profile:skills');
    spy.mockRestore();
  });
});
