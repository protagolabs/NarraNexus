/**
 * Row-menu popovers must dismiss on interaction anywhere on the page, not
 * only inside the sidebar. A fixed-position backdrop cannot guarantee that:
 * rendered inside an animated row (which keeps a transform), it is laid out
 * against the row instead of the viewport. These tests pin the
 * document-level behaviour that replaced it.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { AgentRowMenu } from '../AgentRowMenu';
import { TeamRowMenu } from '../TeamRowMenu';

const agentMenuProps = {
  agentId: 'a1',
  agentName: 'Analyst',
  isOwner: true,
  isPublic: false,
  showPublicToggle: false,
  onStartEdit: vi.fn(),
  onEditAgent: vi.fn(),
  onClearData: vi.fn(),
  onDelete: vi.fn(),
  onTogglePublic: vi.fn(),
};

const teamMenuProps = {
  onAddAgent: vi.fn(),
  addingAgent: false,
  onRename: vi.fn(),
  onClearData: vi.fn(),
  onDelete: vi.fn(),
};

function openAgentMenu() {
  render(
    <div>
      <AgentRowMenu {...agentMenuProps} />
      <button data-testid="elsewhere">elsewhere</button>
    </div>,
  );
  fireEvent.click(screen.getByRole('button', { name: /options/i }));
  expect(screen.getByText(/rename/i)).toBeInTheDocument();
}

describe('AgentRowMenu dismissal', () => {
  it('closes on pointerdown anywhere outside the menu', () => {
    openAgentMenu();
    fireEvent.pointerDown(screen.getByTestId('elsewhere'));
    expect(screen.queryByText(/rename/i)).not.toBeInTheDocument();
  });

  it('closes on Escape', () => {
    openAgentMenu();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByText(/rename/i)).not.toBeInTheDocument();
  });

  it('stays open on pointerdown inside the menu panel', () => {
    openAgentMenu();
    fireEvent.pointerDown(screen.getByText(/rename/i));
    expect(screen.getByText(/rename/i)).toBeInTheDocument();
  });

  it('notifies the host row when dismissed from outside', () => {
    const onOpenChange = vi.fn();
    render(
      <div>
        <AgentRowMenu {...agentMenuProps} onOpenChange={onOpenChange} />
        <button data-testid="elsewhere">elsewhere</button>
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: /options/i }));
    expect(onOpenChange).toHaveBeenLastCalledWith(true);
    fireEvent.pointerDown(screen.getByTestId('elsewhere'));
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
  });
});

describe('TeamRowMenu dismissal', () => {
  it('closes on pointerdown anywhere outside the menu', () => {
    render(
      <div>
        <TeamRowMenu {...teamMenuProps} />
        <button data-testid="elsewhere">elsewhere</button>
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: /options/i }));
    expect(screen.getByText(/rename/i)).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByTestId('elsewhere'));
    expect(screen.queryByText(/rename/i)).not.toBeInTheDocument();
  });
});
