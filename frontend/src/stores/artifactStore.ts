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
const MINIMIZED_IDS_KEY = 'artifact_minimized_ids';

/**
 * Minimal interface to the echarts instance methods we actually use.
 * Avoids a hard type dependency on echarts in this eagerly-loaded store
 * (echarts itself is lazy-loaded inside ChartRenderer).
 */
export interface ChartInstanceLike {
  getDataURL: (opts: {
    type?: 'png' | 'jpeg' | 'svg';
    backgroundColor?: string;
    pixelRatio?: number;
  }) => string;
}

interface ArtifactState {
  artifacts: Artifact[];
  activeArtifactId: string | null;
  collapsed: boolean;

  /**
   * Live registry of mounted chart renderers, keyed by artifact_id.
   * Used by ArtifactDownloadMenu to call getDataURL() for PNG/JPEG export.
   * ChartRenderer registers on mount, unregisters on unmount.
   */
  chartInstances: Record<string, ChartInstanceLike | null>;

  /**
   * Tab IDs the user has clicked "minimize" on. The artifact stays in `artifacts`
   * (and in the DB), but TabStrip filters them out and surfaces them in the
   * "Minimized" header bar so the user can restore them. Persisted to
   * localStorage so refreshes do not undo the user's intent.
   */
  minimizedTabIds: Set<string>;

  loadForSession: (agentId: string, sessionId: string) => Promise<void>;
  loadPinned: (agentId: string) => Promise<void>;
  setActive: (artifactId: string | null) => void;
  upsert: (artifact: Artifact) => void;
  remove: (artifactId: string) => void;
  setCollapsed: (collapsed: boolean) => void;
  registerChartInstance: (artifactId: string, instance: ChartInstanceLike | null) => void;
  minimizeTab: (artifactId: string) => void;
  restoreTab: (artifactId: string) => void;

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

const initialMinimizedTabIds = (() => {
  try {
    const raw = window.localStorage.getItem(MINIMIZED_IDS_KEY);
    if (!raw) return new Set<string>();
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return new Set<string>();
    return new Set<string>(arr.filter((x): x is string => typeof x === 'string'));
  } catch {
    return new Set<string>();
  }
})();

function persistMinimizedTabIds(ids: Set<string>): void {
  try {
    window.localStorage.setItem(MINIMIZED_IDS_KEY, JSON.stringify(Array.from(ids)));
  } catch {
    /* ignore */
  }
}

export const useArtifactStore = create<ArtifactState>((set, get) => ({
  artifacts: [],
  activeArtifactId: null,
  collapsed: initialCollapsed,
  chartInstances: {},
  minimizedTabIds: initialMinimizedTabIds,

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

  registerChartInstance(artifactId, instance) {
    set((state) => ({
      chartInstances: { ...state.chartInstances, [artifactId]: instance },
    }));
  },

  minimizeTab(artifactId) {
    const next = new Set(get().minimizedTabIds);
    next.add(artifactId);
    persistMinimizedTabIds(next);
    // If this was the active tab, switch active to the first non-minimized one.
    const visible = get().artifacts.filter((a) => !next.has(a.artifact_id));
    const currentActive = get().activeArtifactId;
    set({
      minimizedTabIds: next,
      activeArtifactId:
        currentActive === artifactId ? visible[0]?.artifact_id ?? null : currentActive,
    });
  },

  restoreTab(artifactId) {
    const next = new Set(get().minimizedTabIds);
    next.delete(artifactId);
    persistMinimizedTabIds(next);
    set({ minimizedTabIds: next, activeArtifactId: artifactId });
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
