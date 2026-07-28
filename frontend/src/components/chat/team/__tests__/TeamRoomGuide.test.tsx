/**
 * @file_name: TeamRoomGuide.test.tsx
 * @description: The addressing guide's visibility contract.
 *
 * The guide replaces a placeholder line that (a) said the opposite of what the
 * room now does — an un-addressed message goes to the default responder, it is
 * not dropped — and (b) vanished forever once the room had one message. So the
 * two things worth pinning are: it names WHO answers, and folding it is a
 * decision the user makes once and can always undo.
 */
import { afterEach, describe, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { TeamRoomGuide } from '../TeamRoomGuide';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, v?: Record<string, unknown>) =>
      v ? `${k}(${Object.values(v).join(',')})` : k,
  }),
}));

afterEach(() => localStorage.clear());

describe('TeamRoomGuide', () => {
  test('opens on a first visit and names the default responder', () => {
    render(<TeamRoomGuide teamId="team_1" leadName="Ana" />);
    expect(screen.getByText('chat.team.guide.plainWithLead(Ana)')).toBeTruthy();
    expect(screen.getByText('chat.team.guide.mention')).toBeTruthy();
    expect(screen.getByText('chat.team.guide.broadcast')).toBeTruthy();
  });

  test('falls back to a generic line when the team has no members', () => {
    render(<TeamRoomGuide teamId="team_1" leadName={null} />);
    expect(screen.getByText('chat.team.guide.plain')).toBeTruthy();
  });

  test('folding is remembered per team', () => {
    const { unmount } = render(<TeamRoomGuide teamId="team_1" leadName="Ana" />);
    fireEvent.click(screen.getByText('chat.team.guide.title'));
    expect(screen.queryByText(/guide\.mention/)).toBeNull();
    unmount();

    render(<TeamRoomGuide teamId="team_1" leadName="Ana" />);
    expect(screen.queryByText(/guide\.mention/)).toBeNull();
  });

  test('a fold in one room does not silence another', () => {
    const { unmount } = render(<TeamRoomGuide teamId="team_1" leadName="Ana" />);
    fireEvent.click(screen.getByText('chat.team.guide.title'));
    unmount();

    render(<TeamRoomGuide teamId="team_2" leadName="Bo" />);
    expect(screen.getByText('chat.team.guide.plainWithLead(Bo)')).toBeTruthy();
  });

  test('the toggle stays reachable after folding', () => {
    render(<TeamRoomGuide teamId="team_1" leadName="Ana" />);
    const toggle = screen.getByText('chat.team.guide.title');
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(screen.getByText('chat.team.guide.mention')).toBeTruthy();
  });

  test('storage being unavailable degrades to showing the guide', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });

    render(<TeamRoomGuide teamId="team_1" leadName="Ana" />);
    expect(screen.getByText('chat.team.guide.mention')).toBeTruthy();
    // The toggle must still work even though the preference can't persist.
    fireEvent.click(screen.getByText('chat.team.guide.title'));
    expect(screen.queryByText(/guide\.mention/)).toBeNull();

    getItem.mockRestore();
    setItem.mockRestore();
  });
});
