/**
 * @file_name: runtimeStore.ts
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Runtime configuration store
 *
 * Manages app mode, user type, and derived feature flags.
 * Persists mode, userType, and cloudApiUrl to localStorage.
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { AppMode, UserType, FeatureFlags } from '@/types/platform';

interface RuntimeState {
  mode: AppMode | null;
  userType: UserType;
  features: FeatureFlags;
  cloudApiUrl: string;

  setMode: (mode: AppMode | null) => void;
  setUserType: (type: UserType) => void;
  setCloudApiUrl: (url: string) => void;
  /** @deprecated No longer used — kept for backwards compat with persisted state */
  initialize: () => void;
}

function deriveFeatures(
  mode: AppMode | null,
  userType: UserType,
): FeatureFlags {
  if (mode === 'local') {
    return {
      canUseClaudeCode: true,
      canUseApiMode: true,
      showSystemPage: true,
      showSetupWizard: false,
    };
  }

  if (userType === 'internal') {
    return {
      canUseClaudeCode: true,
      canUseApiMode: true,
      showSystemPage: false,
      showSetupWizard: false,
    };
  }

  // Cloud + external
  return {
    canUseClaudeCode: false,
    canUseApiMode: true,
    showSystemPage: false,
    showSetupWizard: false,
  };
}

export const useRuntimeStore = create<RuntimeState>()(
  persist(
    (set, get) => ({
      mode: null,
      userType: 'internal',
      cloudApiUrl: '',
      features: deriveFeatures(null, 'internal'),

      setMode: (mode) => {
        const { userType } = get();
        set({ mode, features: deriveFeatures(mode, userType) });
      },

      setUserType: (userType) => {
        const { mode } = get();
        set({
          userType,
          features: deriveFeatures(mode, userType),
        });
      },

      setCloudApiUrl: (url) => set({ cloudApiUrl: url.replace(/\/+$/, '') }),

      initialize: () => {
        // No-op — kept so old persisted state with `initialize` calls doesn't crash
      },
    }),
    {
      name: 'narranexus-runtime',
      partialize: (state) => ({
        mode: state.mode,
        userType: state.userType,
        cloudApiUrl: state.cloudApiUrl,
      }),
      merge: (persisted, current) => {
        const p = persisted as Partial<RuntimeState>;
        const mode = p.mode ?? current.mode;
        const userType = p.userType ?? current.userType;
        return {
          ...current,
          mode,
          userType,
          cloudApiUrl: p.cloudApiUrl ?? current.cloudApiUrl,
          features: deriveFeatures(mode, userType),
        };
      },
    },
  ),
);


// =============================================================================
// SINGLE SOURCE OF TRUTH: base URL resolution
// =============================================================================
//
// Both the REST API client (lib/api.ts) and the WebSocket manager
// (services/wsManager.ts) resolve their endpoint URL from here. Every
// call resolves fresh from the store — no caching, no constructor side
// effects — so mode/cloudApiUrl changes take effect immediately.
//
// Resolution order:
//
// 1. Explicit build-time env var (VITE_API_BASE_URL) — highest priority.
//    Used by the cloud-web deployment's Nginx-served build where the
//    frontend is served from the same origin as the API.
//
// 2. Cloud mode (`cloud-app` or `cloud-web`) with a configured cloudApiUrl
//    → use that URL verbatim. In cloud mode the backend's port 8000 is
//    NOT exposed — all traffic goes through port 80 via Nginx which
//    proxies /api/* and /ws/* to the backend.
//
// 3. Local Tauri desktop → http://localhost:8000 (direct to backend).
//
// 4. Dev/vite mode → empty string, letting the Vite dev proxy handle
//    /api/* and /ws/* routing during `npm run dev`.
//
// The baseUrl is always an absolute HTTP URL (with scheme and host)
// or an empty string. It never includes a trailing slash.

function _detectTauri(): boolean {
  if (typeof window === 'undefined') return false;
  const w = window as any;
  if ('__TAURI__' in w || '__TAURI_INTERNALS__' in w) return true;
  if (window.location.protocol === 'tauri:') return true;
  if (window.location.hostname === 'tauri.localhost') return true;
  return false;
}

/**
 * Get the current base URL for HTTP API calls.
 *
 * Returns an absolute URL like "http://35.179.146.61" or
 * "http://localhost:8000", or "" for dev mode with Vite proxy.
 *
 * Safe to call from anywhere — reads the current store state.
 */
export function getApiBaseUrl(): string {
  // 1. Explicit env override
  const envUrl = import.meta.env.VITE_API_BASE_URL;
  if (envUrl) return (envUrl as string).replace(/\/+$/, '');

  // 2. Cloud mode
  const { mode, cloudApiUrl } = useRuntimeStore.getState();
  if ((mode === 'cloud-app' || mode === 'cloud-web') && cloudApiUrl) {
    return cloudApiUrl.replace(/\/+$/, '');
  }

  // 3. Tauri local desktop
  if (_detectTauri()) {
    return 'http://localhost:8000';
  }

  // 4. Dev mode — empty string, Vite proxy handles routing
  return '';
}

/**
 * Get the WebSocket base URL derived from the current HTTP base URL.
 *
 * Converts http:// → ws://, https:// → wss://. If the HTTP base URL
 * is empty (dev mode), derives from window.location so Vite proxy works.
 */
export function getWsBaseUrl(): string {
  const httpBase = getApiBaseUrl();
  if (httpBase) {
    return httpBase.replace(/^http(s?):\/\//i, (_m, s) => `ws${s}://`);
  }
  // Dev mode fallback: use current page origin as WS host
  if (typeof window !== 'undefined') {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}`;
  }
  return '';
}
