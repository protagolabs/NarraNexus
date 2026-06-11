/**
 * @file_name: bookmarkStrip.test.tsx
 * @date: 2026-06-10
 * @description: Tests for BookmarkStrip and BookmarkDrawer components.
 *
 * Test strategy:
 *   - Use the real bookmarkStore (actions + clearAll in beforeEach).
 *   - Test visual / behavioral outcomes, not implementation details.
 *   - Drawer Esc and outside-click close, pinned-mode has no backdrop.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { BookmarkStrip } from '../BookmarkStrip';
import { BookmarkDrawer } from '../BookmarkDrawer';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderStrip(overrides: Partial<React.ComponentProps<typeof BookmarkStrip>> = {}) {
  const onOpen = vi.fn();
  const result = render(
    <BookmarkStrip
      agentId="agent1"
      activeTab={null}
      onOpen={onOpen}
      {...overrides}
    />
  );
  return { ...result, onOpen };
}

// ---------------------------------------------------------------------------
// BookmarkStrip — Big bookmark aggregate state
// ---------------------------------------------------------------------------

describe('BookmarkStrip – big bookmark aggregate state', () => {
  beforeEach(() => {
    act(() => {
      useBookmarkStore.getState().clearAll();
    });
  });

  it('activity big bookmark shows carbon pulse when any activity sub-bookmark has attention status', () => {
    act(() => {
      useBookmarkStore.getState().noteJobFailed('agent1', 'job1', 'Morning Brief');
    });
    renderStrip();
    // The activity big bookmark button should have animate-pulse class or carbon color
    const activityBtn = screen.getByRole('button', { name: /activity/i });
    // The element or its children should signal carbon pulse
    expect(activityBtn.innerHTML).toMatch(/animate-pulse|carbon/i);
  });

  it('activity big bookmark shows static yellow dot when only info highlights (no attention)', () => {
    act(() => {
      useBookmarkStore.getState().noteJobCompleted('agent1', 'job1', 'Morning Brief');
    });
    renderStrip();
    const activityBtn = screen.getByRole('button', { name: /activity/i });
    // No pulse for info-only; yellow dot indicator should be present but no animate-pulse
    // We look for yellow-500 marker in inner HTML
    expect(activityBtn.innerHTML).toMatch(/yellow-500/i);
    expect(activityBtn.innerHTML).not.toMatch(/animate-pulse/i);
  });

  it('attention beats info: carbon pulse shown when both attention and info highlights exist', () => {
    act(() => {
      useBookmarkStore.getState().noteJobFailed('agent1', 'job1', 'Job A');
      useBookmarkStore.getState().noteJobCompleted('agent1', 'job2', 'Job B');
    });
    renderStrip();
    const activityBtn = screen.getByRole('button', { name: /activity/i });
    expect(activityBtn.innerHTML).toMatch(/animate-pulse/i);
  });
});

// ---------------------------------------------------------------------------
// BookmarkStrip — Activity badge sum
// ---------------------------------------------------------------------------

describe('BookmarkStrip – activity badge sum', () => {
  beforeEach(() => {
    act(() => {
      useBookmarkStore.getState().clearAll();
    });
  });

  it('shows badge = failedJobs + inboxUnread when > 0', () => {
    act(() => {
      useBookmarkStore.getState().noteJobFailed('agent1', 'job1', 'Job A');
      useBookmarkStore.getState().noteJobFailed('agent1', 'job2', 'Job B');
      useBookmarkStore.getState().noteInboxUnread('agent1', 3);
    });
    renderStrip();
    // badge should show 5 (2 failed + 3 unread)
    expect(screen.getByText('5')).toBeInTheDocument();
  });

  it('does not render badge when failedJobs + inboxUnread === 0', () => {
    renderStrip();
    // No badge text visible for a clean state
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('shows badge of 1 for a single failed job', () => {
    act(() => {
      useBookmarkStore.getState().noteJobFailed('agent1', 'job1', 'Job A');
    });
    renderStrip();
    expect(screen.getByText('1')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// BookmarkStrip — Key-prefix routing (job:/inbox → activity; profile: → agent)
// ---------------------------------------------------------------------------

describe('BookmarkStrip – sub-bookmark routing by key prefix', () => {
  beforeEach(() => {
    act(() => {
      useBookmarkStore.getState().clearAll();
    });
  });

  it('job: key sub-bookmarks appear under the activity section', () => {
    act(() => {
      useBookmarkStore.getState().noteJobRunning('agent1', 'job1', 'Morning Brief');
    });
    renderStrip();
    // The activity section should show a sub-bookmark with "Morning Brief"
    expect(screen.getByLabelText('Morning Brief')).toBeInTheDocument();
  });

  it('inbox key sub-bookmark appears under the activity section', () => {
    act(() => {
      useBookmarkStore.getState().noteInboxUnread('agent1', 2);
    });
    renderStrip();
    // Inbox sub-bookmark label contains "Inbox"
    expect(screen.getByLabelText(/Inbox/i)).toBeInTheDocument();
  });

  it('profile: key sub-bookmarks appear under the agent section', () => {
    act(() => {
      useBookmarkStore.getState().noteProfileUpdate('agent1', 'Awareness');
    });
    renderStrip();
    expect(screen.getByLabelText('Awareness')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// BookmarkStrip — Overflow "+k" entry
// ---------------------------------------------------------------------------

describe('BookmarkStrip – overflow +k aggregation', () => {
  beforeEach(() => {
    act(() => {
      useBookmarkStore.getState().clearAll();
    });
  });

  it('renders +k overflow entry when sub-bookmarks exceed max=3', () => {
    act(() => {
      useBookmarkStore.getState().noteJobRunning('agent1', 'job1', 'Job 1');
      useBookmarkStore.getState().noteJobRunning('agent1', 'job2', 'Job 2');
      useBookmarkStore.getState().noteJobRunning('agent1', 'job3', 'Job 3');
      useBookmarkStore.getState().noteJobRunning('agent1', 'job4', 'Job 4');
    });
    renderStrip();
    // Should show "+1" overflow entry
    expect(screen.getByText('+1')).toBeInTheDocument();
  });

  it('does not show overflow when sub-bookmarks are exactly at max (3)', () => {
    act(() => {
      useBookmarkStore.getState().noteJobRunning('agent1', 'job1', 'Job 1');
      useBookmarkStore.getState().noteJobRunning('agent1', 'job2', 'Job 2');
      useBookmarkStore.getState().noteJobRunning('agent1', 'job3', 'Job 3');
    });
    renderStrip();
    expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// BookmarkStrip — onOpen payloads
// ---------------------------------------------------------------------------

describe('BookmarkStrip – onOpen payloads', () => {
  beforeEach(() => {
    act(() => {
      useBookmarkStore.getState().clearAll();
    });
  });

  it('clicking activity big bookmark calls onOpen with {tab: "activity"}', () => {
    const { onOpen } = renderStrip();
    const activityBtn = screen.getByRole('button', { name: /activity/i });
    fireEvent.click(activityBtn);
    expect(onOpen).toHaveBeenCalledWith({ tab: 'activity' });
  });

  it('clicking agent big bookmark calls onOpen with {tab: "agent"}', () => {
    const { onOpen } = renderStrip();
    const agentBtn = screen.getByRole('button', { name: /^agent$/i });
    fireEvent.click(agentBtn);
    expect(onOpen).toHaveBeenCalledWith({ tab: 'agent' });
  });

  it('clicking a job: sub-bookmark calls onOpen with {tab, key}', () => {
    act(() => {
      useBookmarkStore.getState().noteJobRunning('agent1', 'job1', 'Morning Brief');
    });
    const { onOpen } = renderStrip();
    const subBtn = screen.getByRole('button', { name: /morning brief/i });
    fireEvent.click(subBtn);
    expect(onOpen).toHaveBeenCalledWith({ tab: 'activity', key: 'job:job1' });
  });

  it('clicking an inbox sub-bookmark calls onOpen with {tab: "activity", key: "inbox"}', () => {
    act(() => {
      useBookmarkStore.getState().noteInboxUnread('agent1', 1);
    });
    const { onOpen } = renderStrip();
    const subBtn = screen.getByRole('button', { name: /inbox/i });
    fireEvent.click(subBtn);
    expect(onOpen).toHaveBeenCalledWith({ tab: 'activity', key: 'inbox' });
  });

  it('clicking a profile: sub-bookmark calls onOpen with {tab: "agent", key}', () => {
    act(() => {
      useBookmarkStore.getState().noteProfileUpdate('agent1', 'Awareness');
    });
    const { onOpen } = renderStrip();
    const subBtn = screen.getByRole('button', { name: /awareness/i });
    fireEvent.click(subBtn);
    expect(onOpen).toHaveBeenCalledWith({ tab: 'agent', key: 'profile:Awareness' });
  });
});

// ---------------------------------------------------------------------------
// BookmarkDrawer — Esc and outside-click close
// ---------------------------------------------------------------------------

describe('BookmarkDrawer – close behaviors', () => {
  it('calls onClose when Escape is pressed', () => {
    const onClose = vi.fn();
    render(
      <BookmarkDrawer
        open={true}
        pinned={false}
        onPinnedChange={vi.fn()}
        onClose={onClose}
        title="ACTIVITY"
      >
        <div>content</div>
      </BookmarkDrawer>
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('calls onClose when clicking the backdrop', () => {
    const onClose = vi.fn();
    render(
      <BookmarkDrawer
        open={true}
        pinned={false}
        onPinnedChange={vi.fn()}
        onClose={onClose}
        title="ACTIVITY"
      >
        <div>content</div>
      </BookmarkDrawer>
    );
    // The backdrop should have role or be a clickable area
    const backdrop = document.body.querySelector('[data-drawer-backdrop]');
    expect(backdrop).toBeTruthy();
    fireEvent.click(backdrop!);
    expect(onClose).toHaveBeenCalled();
  });

  it('calls onClose when clicking the X button', () => {
    const onClose = vi.fn();
    render(
      <BookmarkDrawer
        open={true}
        pinned={false}
        onPinnedChange={vi.fn()}
        onClose={onClose}
        title="ACTIVITY"
      >
        <div>content</div>
      </BookmarkDrawer>
    );
    const closeBtn = screen.getByRole('button', { name: /close/i });
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// BookmarkDrawer — Pinned mode has no backdrop
// ---------------------------------------------------------------------------

describe('BookmarkDrawer – pinned mode', () => {
  it('does not render a backdrop when pinned=true', () => {
    render(
      <BookmarkDrawer
        open={true}
        pinned={true}
        onPinnedChange={vi.fn()}
        onClose={vi.fn()}
        title="ACTIVITY"
      >
        <div>content</div>
      </BookmarkDrawer>
    );
    const backdrop = document.body.querySelector('[data-drawer-backdrop]');
    expect(backdrop).toBeNull();
  });

  it('renders without role=dialog aria-modal in pinned mode', () => {
    render(
      <BookmarkDrawer
        open={true}
        pinned={true}
        onPinnedChange={vi.fn()}
        onClose={vi.fn()}
        title="ACTIVITY"
      >
        <div>content</div>
      </BookmarkDrawer>
    );
    // In pinned mode it's a static column frame, no aria-modal
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders role=dialog in slide-over (unpinned) mode', () => {
    render(
      <BookmarkDrawer
        open={true}
        pinned={false}
        onPinnedChange={vi.fn()}
        onClose={vi.fn()}
        title="ACTIVITY"
      >
        <div>content</div>
      </BookmarkDrawer>
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('renders pin toggle button', () => {
    const onPinnedChange = vi.fn();
    render(
      <BookmarkDrawer
        open={true}
        pinned={false}
        onPinnedChange={onPinnedChange}
        onClose={vi.fn()}
        title="ACTIVITY"
      >
        <div>content</div>
      </BookmarkDrawer>
    );
    const pinBtn = screen.getByRole('button', { name: /pin/i });
    fireEvent.click(pinBtn);
    expect(onPinnedChange).toHaveBeenCalledWith(true);
  });

  it('clicking pin button when pinned=true calls onPinnedChange with false', () => {
    const onPinnedChange = vi.fn();
    render(
      <BookmarkDrawer
        open={true}
        pinned={true}
        onPinnedChange={onPinnedChange}
        onClose={vi.fn()}
        title="ACTIVITY"
      >
        <div>content</div>
      </BookmarkDrawer>
    );
    const pinBtn = screen.getByRole('button', { name: /unpin/i });
    fireEvent.click(pinBtn);
    expect(onPinnedChange).toHaveBeenCalledWith(false);
  });
});
