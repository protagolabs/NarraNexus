/**
 * teamsStore - Subproject 1: Team Membership state
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api, ApiError } from '@/lib/api';
import type { TeamWithMembers } from '@/types';

interface TeamsState {
  teams: TeamWithMembers[];
  loading: boolean;
  loaded: boolean;

  refresh: () => Promise<void>;
  createTeam: (payload: { name: string; description?: string; color?: string }) => Promise<string | null>;
  updateTeam: (teamId: string, patch: { name?: string; description?: string; color?: string; intro_md?: string; lead_agent_id?: string }) => Promise<void>;
  deleteTeam: (teamId: string) => Promise<void>;
  addMember: (teamId: string, agentId: string) => Promise<void>;
  removeMember: (teamId: string, agentId: string) => Promise<void>;

  /** Patrol switch per team — ONE copy. The room poll and the work board
   *  poll both write it (`notePatrol`); the management tab reads it and
   *  writes through `setPatrol`. Not persisted: it is server state. */
  patrolByTeam: Record<string, boolean>;
  notePatrol: (teamId: string, enabled: boolean) => void;
  setPatrol: (teamId: string, enabled: boolean) => Promise<void>;

  // selectors
  teamsForAgent: (agentId: string) => TeamWithMembers[];
}

export const useTeamsStore = create<TeamsState>()(
  persist(
    (set, get) => ({
      teams: [],
      loading: false,
      loaded: false,

      refresh: async () => {
        set({ loading: true });
        try {
          const r = await api.listTeams();
          set({ teams: r.teams, loaded: true });
        } catch (e) {
          console.error('listTeams failed', e);
        } finally {
          set({ loading: false });
        }
      },

      createTeam: async (payload) => {
        const r = await api.createTeam(payload);
        await get().refresh();
        return r.team?.team_id || null;
      },

      updateTeam: async (teamId, patch) => {
        await api.updateTeam(teamId, patch);
        await get().refresh();
      },

      deleteTeam: async (teamId) => {
        try {
          await api.deleteTeam(teamId);
        } catch (e) {
          // 404 = the team is already gone server-side; only the persisted
          // localStorage cache still shows it. Rethrowing here would skip
          // refresh() and trap the user in delete -> 404 -> still-shown.
          if (!(e instanceof ApiError && e.status === 404)) throw e;
        }
        await get().refresh();
      },

      addMember: async (teamId, agentId) => {
        await api.addTeamMember(teamId, agentId);
        await get().refresh();
      },

      removeMember: async (teamId, agentId) => {
        await api.removeTeamMember(teamId, agentId);
        await get().refresh();
      },

      patrolByTeam: {},
      notePatrol: (teamId, enabled) =>
        set((s) =>
          s.patrolByTeam[teamId] === enabled
            ? s
            : { patrolByTeam: { ...s.patrolByTeam, [teamId]: enabled } },
        ),
      setPatrol: async (teamId, enabled) => {
        const prev = get().patrolByTeam[teamId];
        get().notePatrol(teamId, enabled); // optimistic
        try {
          await api.setTeamPatrol(teamId, enabled);
        } catch (e) {
          if (prev !== undefined) get().notePatrol(teamId, prev);
          throw e;
        }
      },

      teamsForAgent: (agentId) => {
        return get().teams.filter((t) => t.member_agent_ids.includes(agentId));
      },
    }),
    {
      name: 'narra-nexus-teams',
      partialize: (s) => ({ teams: s.teams, loaded: s.loaded }),
    }
  )
);
