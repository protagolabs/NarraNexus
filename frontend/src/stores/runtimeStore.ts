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

      setCloudApiUrl: (url) => set({ cloudApiUrl: url }),

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
