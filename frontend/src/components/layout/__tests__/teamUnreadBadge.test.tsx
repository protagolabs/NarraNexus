/**
 * @file_name: teamUnreadBadge.test.tsx
 * @description: The sidebar telling the user a room they left has spoken.
 *
 * A team room is an ASYNC space by design — the user hands it work and leaves.
 * Before this, leaving was a one-way door: the row looked identical whether six
 * agents had been talking for ten minutes or nothing had happened, so the only
 * way to find out was to open every room and read.
 *
 * These tests drive the real `AgentList`, not the pure helpers, because the
 * helpers are already unit-tested in `lib/__tests__/unread.test.ts` and the way
 * this feature would actually fail is at the seam: a mark computed correctly and
 * passed to nothing, or a read-marker never advanced so the dot never clears.
 * This branch has already shipped one feature wired to nothing with every test
 * green, which is the reason this file renders the component.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/lib/api', () => ({
  api: {
    getAgents: vi.fn().mockResolvedValue({ success: true, agents: [], count: 0 }),
    getTeams: vi.fn().mockResolvedValue({ success: true, teams: [] }),
  },
}));

import { AgentList } from '../AgentList';
import { useConfigStore, useChatStore, useTeamsStore } from '@/stores';
import { markTeamRead } from '@/lib/unread';
import type { TeamWithMembers } from '@/types/teams';

const TEAM_ID = 'team_desk';
const SPOKE_AT = '2026-08-13T09:05:00Z';

function team(extra: Partial<TeamWithMembers> = {}): TeamWithMembers {
  return {
    team: {
      team_id: TEAM_ID,
      owner_user_id: 'usr_1',
      name: 'Desk',
      source: 'local',
    },
    member_agent_ids: ['agent_a'],
    ...extra,
  };
}

beforeEach(() => {
  localStorage.clear();
  useConfigStore.setState({ userId: '', agentId: '', agents: [] });
  useChatStore.setState({ agentSessions: {} });
  useTeamsStore.setState({ teams: [], loaded: true });
});

const renderList = (collapsed = false) =>
  render(
    <MemoryRouter initialEntries={['/app']}>
      <AgentList collapsed={collapsed} />
    </MemoryRouter>,
  );

const dot = () => screen.queryByTestId(`team-unread-${TEAM_ID}`);

describe('the sidebar marks a team room that has spoken', () => {
  it('shows nothing for a room that has never said anything', () => {
    // Every team the user created and never used would otherwise carry a mark
    // from the moment it existed.
    useTeamsStore.setState({ teams: [team()], loaded: true });
    renderList();

    expect(dot()).toBeNull();
  });

  it('marks a room whose newest message postdates the last read', () => {
    markTeamRead(TEAM_ID, Date.parse('2026-08-13T09:00:00Z'));
    useTeamsStore.setState({
      teams: [team({ last_message_at: SPOKE_AT, last_message_preview: 'done' })],
      loaded: true,
    });
    renderList();

    expect(dot()).not.toBeNull();
  });

  it('marks a room the user has never opened at all', () => {
    // Marker 0 means "nothing read", not "everything read" — an agent that
    // answered before the room was ever opened is the case the mark exists for.
    useTeamsStore.setState({ teams: [team({ last_message_at: SPOKE_AT })], loaded: true });
    renderList();

    expect(dot()).not.toBeNull();
  });

  it('does not mark a room the user has caught up on', () => {
    markTeamRead(TEAM_ID, Date.parse(SPOKE_AT));
    useTeamsStore.setState({ teams: [team({ last_message_at: SPOKE_AT })], loaded: true });
    renderList();

    expect(dot()).toBeNull();
  });

  it('does not mark the room that is currently open', () => {
    // The user is looking at it. A mark on the row you are reading is noise, and
    // it is also what made the agent badge look broken before it was fixed.
    useTeamsStore.setState({ teams: [team({ last_message_at: SPOKE_AT })], loaded: true });
    render(
      <MemoryRouter initialEntries={[`/app/teams/${TEAM_ID}/chat`]}>
        <AgentList collapsed={false} />
      </MemoryRouter>,
    );

    expect(dot()).toBeNull();
  });

  it('clears the mark once the room has been open', () => {
    // The durable half: opening the room writes the watermark, so the mark stays
    // cleared after navigating away. Reading it back through a fresh render is
    // what proves the marker was persisted rather than held in component state.
    useTeamsStore.setState({ teams: [team({ last_message_at: SPOKE_AT })], loaded: true });
    const open = render(
      <MemoryRouter initialEntries={[`/app/teams/${TEAM_ID}/chat`]}>
        <AgentList collapsed={false} />
      </MemoryRouter>,
    );
    open.unmount();

    renderList();
    expect(dot()).toBeNull();
  });

  it('marks the room in the collapsed rail too', () => {
    // The rail is the sidebar most of the time on a narrow window. A mark that
    // only exists when expanded is a mark the user does not see.
    useTeamsStore.setState({ teams: [team({ last_message_at: SPOKE_AT })], loaded: true });
    renderList(true);

    expect(dot()).not.toBeNull();
  });

  it('shows who spoke and what they said', () => {
    // The mark says "something happened"; the preview is what makes the row
    // worth reading without opening it — the same line the agent rows carry
    // directly beneath it.
    useTeamsStore.setState({
      teams: [
        team({
          last_message_at: SPOKE_AT,
          last_message_preview: 'deployed to staging',
          last_message_author: 'Ada',
        }),
      ],
      loaded: true,
    });
    renderList();

    expect(screen.getByText(/deployed to staging/)).toBeTruthy();
    expect(screen.getByText(/Ada/)).toBeTruthy();
  });

  it('re-marks the room when it speaks again after being read', () => {
    markTeamRead(TEAM_ID, Date.parse(SPOKE_AT));
    useTeamsStore.setState({ teams: [team({ last_message_at: SPOKE_AT })], loaded: true });
    renderList();
    expect(dot()).toBeNull();

    act(() => {
      useTeamsStore.setState({
        teams: [team({ last_message_at: '2026-08-13T10:00:00Z' })],
        loaded: true,
      });
    });

    expect(dot()).not.toBeNull();
  });
});
