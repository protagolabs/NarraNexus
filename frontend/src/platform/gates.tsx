/**
 * @file_name: gates.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Lazy gates the loader registers for a plugin's declared pages/panels: they fire the activation event, then render what the plugin registered.
 */
import { useEffect, useSyncExternalStore, type ComponentType } from 'react';

import { activationState, fireActivation, subscribeActivation } from './activation';
import { PAGES, PANELS, type PanelProps } from './registries';

function useActivation(pluginId: string) {
  return useSyncExternalStore(subscribeActivation, () => activationState(pluginId), () => activationState(pluginId));
}

function Status({ pluginId, state, error }: { pluginId: string; state: string; error?: string }) {
  if (state === 'failed') {
    return (
      <div role="alert" className="p-4 text-sm text-[var(--color-error)]">
        Plugin {pluginId} failed to activate: {error}
      </div>
    );
  }
  return <div className="p-4 text-sm text-[var(--nm-ink50)]">Loading plugin {pluginId}…</div>;
}

export function makePageGate(pluginId: string, pageId: string): ComponentType<Record<string, never>> {
  const Gate = () => {
    const { state, error } = useActivation(pluginId);
    useEffect(() => {
      void fireActivation(`onPage:${pageId}`);
    }, []);
    const entry = PAGES.list().find((e) => e.id === pageId && e.owner === pluginId && e.value.element !== null && e.value.element !== Gate);
    if (state === 'active' && entry?.value.element) {
      const Real = entry.value.element;
      return <Real />;
    }
    return <Status pluginId={pluginId} state={state} error={error} />;
  };
  Gate.displayName = `PluginPageGate(${pageId})`;
  return Gate;
}

export function makePanelGate(pluginId: string, panelId: string): ComponentType<PanelProps> {
  const Gate = (props: PanelProps) => {
    const { state, error } = useActivation(pluginId);
    useEffect(() => {
      void fireActivation(`onPanel:${panelId}`);
    }, []);
    const entry = PANELS.list().find((e) => e.id === panelId && e.owner === pluginId && e.value.component !== Gate);
    if (state === 'active' && entry) {
      const Real = entry.value.component;
      return <Real {...props} />;
    }
    return <Status pluginId={pluginId} state={state} error={error} />;
  };
  Gate.displayName = `PluginPanelGate(${panelId})`;
  return Gate;
}
