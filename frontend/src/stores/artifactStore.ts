/**
 * Artifact store — manages open artifact tabs, active selection, and collapse state.
 *
 * Real-time artifact signals arrive via the existing chat WebSocket stream (tool_output
 * frames parsed in ChatPanel.tsx). This store does NOT manage a dedicated WS connection.
 */

import { create } from 'zustand';
import type { Artifact } from '@/types/artifact';
import { artifactsApi } from '@/services/artifactsApi';

const COLLAPSED_KEY = 'artifact_column_collapsed';

interface ArtifactState {
  artifacts: Artifact[];
  activeArtifactId: string | null;
  collapsed: boolean;

  loadForSession: (agentId: string, sessionId: string) => Promise<void>;
  loadPinned: (agentId: string) => Promise<void>;
  setActive: (artifactId: string | null) => void;
  upsert: (artifact: Artifact) => void;
  remove: (artifactId: string) => void;
  setCollapsed: (collapsed: boolean) => void;

  pin: (agentId: string, artifactId: string, pinned: boolean) => Promise<void>;
  delete: (agentId: string, artifactId: string) => Promise<void>;
}

const initialCollapsed = (() => {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
})();

export const useArtifactStore = create<ArtifactState>((set, get) => ({
  artifacts: [],
  activeArtifactId: null,
  collapsed: initialCollapsed,

  async loadForSession(agentId, sessionId) {
    const sessionArtifacts = await artifactsApi.listSession(agentId, sessionId);
    const pinned = await artifactsApi.listPinned(agentId);
    const merged = [
      ...pinned,
      ...sessionArtifacts.filter((a) => !pinned.find((p) => p.artifact_id === a.artifact_id)),
    ];
    set({
      artifacts: merged,
      activeArtifactId: merged[0]?.artifact_id ?? null,
    });
  },

  async loadPinned(agentId) {
    const pinned = await artifactsApi.listPinned(agentId);
    set({ artifacts: pinned, activeArtifactId: pinned[0]?.artifact_id ?? null });
  },

  setActive(artifactId) {
    set({ activeArtifactId: artifactId });
  },

  upsert(artifact) {
    const list = get().artifacts;
    const idx = list.findIndex((a) => a.artifact_id === artifact.artifact_id);
    if (idx === -1) {
      set({ artifacts: [artifact, ...list], activeArtifactId: artifact.artifact_id });
    } else {
      const next = [...list];
      next[idx] = artifact;
      set({ artifacts: next });
    }
  },

  remove(artifactId) {
    const list = get().artifacts.filter((a) => a.artifact_id !== artifactId);
    set({
      artifacts: list,
      activeArtifactId:
        get().activeArtifactId === artifactId ? list[0]?.artifact_id ?? null : get().activeArtifactId,
    });
  },

  setCollapsed(collapsed) {
    set({ collapsed });
    try {
      window.localStorage.setItem(COLLAPSED_KEY, collapsed ? '1' : '0');
    } catch {
      /* ignore */
    }
  },

  async pin(agentId, artifactId, pinned) {
    const updated = await artifactsApi.setPinned(agentId, artifactId, pinned);
    get().upsert(updated);
  },

  async delete(agentId, artifactId) {
    await artifactsApi.remove(agentId, artifactId);
    get().remove(artifactId);
  },
}));
