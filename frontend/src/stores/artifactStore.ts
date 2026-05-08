/**
 * Artifact store — manages open artifact tabs, active selection, collapse state, and WS sync
 */

import { create } from 'zustand';
import type { Artifact } from '@/types/artifact';
import { artifactsApi } from '@/services/artifactsApi';

const COLLAPSED_KEY = 'artifact_column_collapsed';

interface ArtifactState {
  artifacts: Artifact[];
  activeArtifactId: string | null;
  collapsed: boolean;
  _ws: WebSocket | null;

  loadForSession: (agentId: string, sessionId: string) => Promise<void>;
  loadPinned: (agentId: string) => Promise<void>;
  setActive: (artifactId: string | null) => void;
  upsert: (artifact: Artifact) => void;
  remove: (artifactId: string) => void;
  setCollapsed: (collapsed: boolean) => void;

  pin: (agentId: string, artifactId: string, pinned: boolean) => Promise<void>;
  delete: (agentId: string, artifactId: string) => Promise<void>;

  connectWs: (agentId: string) => void;
  disconnectWs: () => void;
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
  _ws: null,

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

  connectWs(agentId) {
    get().disconnectWs();
    const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/artifacts/${agentId}`;
    const ws = new WebSocket(url);
    ws.onmessage = async (e) => {
      let evt: { type?: string; artifact_id?: string; pinned?: boolean };
      try {
        evt = JSON.parse(e.data);
      } catch {
        return;
      }
      if (!evt.type || !evt.artifact_id) return;

      if (evt.type === 'artifact.created' || evt.type === 'artifact.updated') {
        try {
          const detail = await artifactsApi.getDetail(agentId, evt.artifact_id);
          const { upsert, setActive, setCollapsed } = get();
          upsert(detail.artifact);
          if (evt.type === 'artifact.created') {
            setActive(evt.artifact_id);
            setCollapsed(false);
          }
        } catch {
          /* ignore — artifact may have been deleted between events */
        }
      } else if (evt.type === 'artifact.pinned') {
        const list = get().artifacts.map((a) =>
          a.artifact_id === evt.artifact_id
            ? { ...a, pinned: !!evt.pinned, session_id: evt.pinned ? null : a.session_id }
            : a,
        );
        set({ artifacts: list });
      } else if (evt.type === 'artifact.deleted') {
        get().remove(evt.artifact_id);
      }
      // ignore "ping" frames
    };
    ws.onclose = () => set({ _ws: null });
    set({ _ws: ws });
  },

  disconnectWs() {
    const ws = get()._ws;
    if (ws) {
      ws.onmessage = null;
      ws.onclose = null;
      try {
        ws.close();
      } catch {
        /* */
      }
    }
    set({ _ws: null });
  },
}));
