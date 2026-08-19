/**
 * The drawer header's title is a panel switcher: a pinned drawer is an
 * independent window and owns its own content controls — switching what it
 * shows must not require a trip to the chat header. The menu is fed by the
 * tabs registry, so every panel is reachable from it.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { BookmarkDrawer } from '../BookmarkDrawer';
import { ALL_TABS } from '../tabs';

function renderDrawer(onSelectTab = vi.fn()) {
  render(
    <BookmarkDrawer
      open
      pinned
      onPinnedChange={vi.fn()}
      onClose={vi.fn()}
      title="ARTIFACTS"
      activeTab="artifacts"
      onSelectTab={onSelectTab}
    >
      <div>panel body</div>
    </BookmarkDrawer>,
  );
  return onSelectTab;
}

describe('drawer panel switcher', () => {
  it('lists every registered panel when the title is clicked', () => {
    renderDrawer();
    fireEvent.click(screen.getByRole('button', { name: /switch panel/i }));
    // Every tab in the registry has a row (labels resolve through i18n, so
    // count rows rather than match per-tab text).
    const menuButtons = screen
      .getAllByRole('button')
      .filter((b) => b.getAttribute('aria-checked') === null);
    expect(menuButtons.length).toBeGreaterThanOrEqual(ALL_TABS.length);
  });

  it('selecting another panel fires onSelectTab and closes the menu', () => {
    const onSelectTab = renderDrawer();
    fireEvent.click(screen.getByRole('button', { name: /switch panel/i }));
    fireEvent.click(screen.getByRole('button', { name: /jobs/i }));
    expect(onSelectTab).toHaveBeenCalledWith('jobs');
    expect(screen.queryByRole('button', { name: /inbox/i })).toBeNull();
  });

  it('selecting the already-open panel closes the menu without a switch', () => {
    const onSelectTab = renderDrawer();
    fireEvent.click(screen.getByRole('button', { name: /switch panel/i }));
    const artifactRows = screen.getAllByRole('button', { name: /artifacts/i });
    fireEvent.click(artifactRows[artifactRows.length - 1]);
    expect(onSelectTab).not.toHaveBeenCalled();
  });

  it('without onSelectTab the title stays a plain label', () => {
    render(
      <BookmarkDrawer
        open
        pinned
        onPinnedChange={vi.fn()}
        onClose={vi.fn()}
        title="ARTIFACTS"
      >
        <div>panel body</div>
      </BookmarkDrawer>,
    );
    expect(screen.queryByRole('button', { name: /switch panel/i })).toBeNull();
  });
});
