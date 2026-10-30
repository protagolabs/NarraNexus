/**
 * Configuration store
 * Manages authentication, agent selection, and app settings
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from '@/lib/api';
import { resetSessionGuard } from '@/lib/sessionGuard';
import { useTeamsStore } from './teamsStore';
import type { AgentInfo } from '@/types';
import { isSafeAgentId } from '@/lib/agentIdentity/agentIdSafety';
import { ownsAnyAgent, pickDefaultAgent } from '@/lib/agentIdentity/defaultAgent';
import { pickGuideAgent } from '@/lib/agentIdentity/guideAgent';

export type { AgentInfo };

interface ConfigState {
  // Auth state
  isLoggedIn: boolean;
  userId: string;
  token: string;  // JWT token (cloud mode only)
  role: string;   // 'user' | 'staff'
  netmindToken: string;  // NetMind loginToken, retained for Phase 2/3 actions
  displayName: string;   // NetMind nickname, for display (userId is opaque hex)
  email: string;         // NetMind account email

  // Agent state
  agentId: string;
  agents: AgentInfo[];
  /** The agent list was fetched from the server in THIS session. `agents` is
   * persisted and may be a previous session's (stale) list; code that must
   * judge against the current list waits for this. Forced false on rehydrate,
   * so a stored value never counts. */
  agentsLoaded: boolean;

  // Awareness update tracking (red dot notification)
  awarenessUpdatedAgents: string[];

  // Actions
  login: (userId: string, token?: string, role?: string, profile?: { displayName?: string; email?: string }) => void;
  setNetmindToken: (token: string) => void;
  logout: () => void;
  setAgentId: (id: string) => void;
  setAgents: (agents: AgentInfo[]) => void;
  refreshAgents: () => Promise<void>;
  checkAwarenessUpdate: (agentId: string) => Promise<void>;
  clearAwarenessUpdate: (agentId: string) => void;
}

export const useConfigStore = create<ConfigState>()(
  persist(
    (set, get) => ({
      // Default values
      isLoggedIn: false,
      userId: '',
      token: '',
      role: '',
      netmindToken: '',
      displayName: '',
      email: '',
      agentId: '',
      agents: [],
      agentsLoaded: false,
      awarenessUpdatedAgents: [],

      // Actions
      login: (userId, token?, role?, profile?) => {
        const prevUserId = get().userId;
        // A fresh credential invalidates the previous session's verdict.
        // Without this, the guard stays latched on "already confirmed
        // dead" and would never probe again for the NEW session.
        resetSessionGuard();
        set({
          isLoggedIn: true,
          agentsLoaded: false,
          userId,
          token: token || '',
          role: role || '',
          displayName: profile?.displayName || '',
          email: profile?.email || '',
        });
        // If we just switched accounts (or just logged in fresh after a
        // logout), wipe per-user persisted caches so the next consumer
        // refetches against the right identity. teamsStore is the only
        // store currently using zustand persist for per-user data — see
        // its frontmatter and `partialize: { teams, loaded }`. Without
        // this reset, AgentList's `if (!teamsLoaded) teamsRefresh()` guard
        // keeps showing the previous user's team sections because
        // `loaded` survives in localStorage.
        if (prevUserId !== userId) {
          useTeamsStore.setState({ teams: [], loaded: false });
        }
      },

      setNetmindToken: (token) => set({ netmindToken: token }),

      logout: () => {
        set({
          isLoggedIn: false,
          userId: '',
          token: '',
          role: '',
          netmindToken: '',
          displayName: '',
          email: '',
          agentId: '',
          agents: [],
          agentsLoaded: false,
          awarenessUpdatedAgents: [],
        });
        // Symmetric reset — see comment in login(). Logging out and back
        // in as the same user still benefits from this (we cleared
        // anyway, refresh will repopulate); logging in as a different
        // user is the actual bug this prevents.
        useTeamsStore.setState({ teams: [], loaded: false });
      },

      // '' clears the selection; anything else must be a possible agent id, or a
      // crafted link / stale state would make every panel request it (see
      // lib/agentIdentity/agentIdSafety.ts).
      setAgentId: (id) => {
        if (id && !isSafeAgentId(id)) {
          // The id is caller- (often link-) controlled: say that it was
          // refused, never what it was.
          console.warn('[agent-id] refusing an unsafe agent id');
          return;
        }
        set({ agentId: id });
      },

      setAgents: (agents) => set({ agents }),

      refreshAgents: async () => {
        const { userId } = get();
        if (!userId) return;
        const selectedAtStart = get().agentId;
        try {
          const res = await api.getAgents();
          // A response for a session that has since logged out / switched
          // user must not land in the new one (it could even select one of the
          // previous user's agents).
          if (get().userId !== userId) return;
          if (res.success) {
            // A selection the server does not list (deleted, foreign, or from a
            // crafted link) would otherwise persist and make every panel request
            // it on every load, a stream of 403/404s. It is cleared, not swapped
            // for another agent: a silent switch mid-chat would send the user's
            // next message to an agent they did not pick (the sidebar's mount
            // pass picks the viewer's own default on the next full page load;
            // within this session it stays empty). Only a
            // selection that was already current when this request started is
            // judged: one made while it was in flight (a just-created agent)
            // may postdate it.
            const { agentId } = get();
            const stale = agentId !== '' && agentId === selectedAtStart
              && !res.agents.some((a) => a.agent_id === agentId);
            // A new user's first agent (the guide) is provisioned after login
            // returns, so login and the sidebar's mount pass found nothing of
            // theirs to select. At the moment the viewer's FIRST own agent
            // appears — none in the list before, some now — with nothing
            // selected before or during this request, it is selected. Keyed
            // on that transition, not on bootstrap_active (which every
            // not-yet-chatted own agent carries): a veteran's cleared selection
            // is never refilled, so "cleared, not swapped" holds.
            const firstOwn = agentId === '' && selectedAtStart === ''
              && !ownsAnyAgent(get().agents, userId) && ownsAnyAgent(res.agents, userId)
              // The guide login made for them first (imports in the welcome
              // flow may be newer); otherwise their newest own agent.
              ? (pickGuideAgent(res.agents, userId)?.agent_id || pickDefaultAgent(res.agents, userId)) : '';
            set({
              agents: res.agents,
              agentsLoaded: true,
              ...(stale ? { agentId: '' } : firstOwn ? { agentId: firstOwn } : {}),
            });
          } else {
            // Keeping the previous list is deliberate (a backend hiccup must
            // not blank the sidebar) — being SILENT about it was not. The
            // endpoint answers 200 + {success:false} for any unhandled
            // handler exception, so this branch is the one where the UI goes
            // on confidently showing names that may already be wrong. Say so.
            console.error('Agent list refresh refused by server:', res.error);
          }
        } catch (err) {
          console.error('Failed to refresh agents:', err);
        }
      },

      checkAwarenessUpdate: async (agentId: string) => {
        try {
          const res = await api.getAwareness(agentId);
          if (res.success && res.update_time) {
            const lastSeen = localStorage.getItem(`lastSeenAwarenessTime:${agentId}`);
            if (!lastSeen || res.update_time > lastSeen) {
              const current = get().awarenessUpdatedAgents;
              if (!current.includes(agentId)) {
                set({ awarenessUpdatedAgents: [...current, agentId] });
              }
            }
          }
        } catch (err) {
          console.error('Failed to check awareness update:', err);
        }
      },

      clearAwarenessUpdate: (agentId: string) => {
        // Store current time as last seen
        localStorage.setItem(`lastSeenAwarenessTime:${agentId}`, new Date().toISOString());
        set({
          awarenessUpdatedAgents: get().awarenessUpdatedAgents.filter((id) => id !== agentId),
        });
      },
    }),
    {
      name: 'narra-nexus-config',
      // A persisted selection from before the check (or a hand-edited one) must
      // not come back unchecked on reload.
      merge: (persisted, current) => {
        const restored = { ...current, ...(persisted as Partial<ConfigState>) };
        if (restored.agentId && !isSafeAgentId(restored.agentId)) restored.agentId = '';
        restored.agentsLoaded = false; // describes this session, never a stored one
        return restored;
      },
    }
  )
);
