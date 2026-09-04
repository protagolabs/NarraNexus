/**
 * @file_name: host.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The `HostAPI` a plugin bundle receives in `activate(host)` — the whole, deliberately small, host surface.
 *
 * A plugin sees: the shared framework libraries (so its ESM bundle can
 * mark them external and use the host's single copies), the registries it
 * may contribute to, a JSON API caller that carries the session auth, an
 * i18n bundle adder scoped to `plugin:<id>`, the error sink, and a
 * disposable stack the loader unwinds on deactivate. Nothing else: stores,
 * components and internals are not promised (Grafana's Angular lesson).
 */
import * as React from 'react';
import * as ReactDOM from 'react-dom';
import * as ReactRouter from 'react-router-dom';
import * as Zustand from 'zustand';
import i18next from 'i18next';
import * as Icons from 'lucide-react';

import { getApiBaseUrl } from '@/stores/runtimeStore';
import { getAuthHeaders } from '@/lib/authHeaders';
import { addPluginBundle, pluginT } from './i18n';
import { reportUiError } from './errorSink';
import {
  AGENT_CARD_BADGES,
  CHAT_HEADER_ACTIONS,
  COMMANDS,
  COMPOSER_EXTENSIONS,
  CONVERSATION_KINDS,
  MESSAGE_ACTIONS,
  MESSAGE_RENDERERS,
  PAGES,
  PANELS,
  SETTINGS_SECTIONS,
  SIDEBAR,
  SIDEBAR_SECTIONS,
  THEMES,
  TIMELINE_EVENTS,
  TOP_BAR_ITEMS,
  type Registry,
} from './registries';

export const HOST_API_VERSION = 1;

export interface Disposable {
  dispose(): void;
}

export interface HostAPI {
  version: string;
  api: { ui: number };
  pluginId: string;
  libs: {
    react: typeof React;
    reactDom: typeof ReactDOM;
    router: typeof ReactRouter;
    zustand: typeof Zustand;
    i18next: typeof i18next;
    icons: typeof Icons;
  };
  registries: {
    pages: typeof PAGES;
    sidebar: typeof SIDEBAR;
    panels: typeof PANELS;
    settingsSections: typeof SETTINGS_SECTIONS;
    commands: typeof COMMANDS;
    themes: typeof THEMES;
    /** Content registries: own the rendering of a message / a timeline event type. */
    messageRenderers: typeof MESSAGE_RENDERERS;
    timelineEvents: typeof TIMELINE_EVENTS;
    /** Slot points inside existing surfaces (gated by `when`, ordered by `order`). */
    conversationKinds: typeof CONVERSATION_KINDS;
    chatHeaderActions: typeof CHAT_HEADER_ACTIONS;
    composerExtensions: typeof COMPOSER_EXTENSIONS;
    messageActions: typeof MESSAGE_ACTIONS;
    sidebarSections: typeof SIDEBAR_SECTIONS;
    agentCardBadges: typeof AGENT_CARD_BADGES;
    topBarItems: typeof TOP_BAR_ITEMS;
  };
  /** Register into any host registry as this plugin; the disposer is tracked. */
  register<T>(registry: Registry<T>, id: string, value: T): Disposable;
  http: {
    /** JSON request against the backend with the session's auth headers; relative paths only. */
    request<T = unknown>(path: string, init?: RequestInit): Promise<T>;
    /** This plugin's own route prefix: `/api/x/<id>`. */
    prefix: string;
  };
  i18n: {
    addResourceBundle(lng: string, resources: Record<string, unknown>): void;
    t(key: string, options?: Record<string, unknown>): string;
  };
  log: { error(error: unknown, context?: string): void };
  subscriptions: Disposable[];
  dispose(): void;
}

declare global {
  interface Window {
    __narranexus_host__?: HostAPI['libs'] & { version: number };
  }
}

/** Publish the framework libraries once so plugin bundles resolve `external` imports to the host copies. */
export function exposeHostGlobals(): void {
  if (typeof window === 'undefined' || window.__narranexus_host__) return;
  window.__narranexus_host__ = {
    version: HOST_API_VERSION,
    react: React,
    reactDom: ReactDOM,
    router: ReactRouter,
    zustand: Zustand,
    i18next,
    icons: Icons,
  };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  if (!path.startsWith('/')) throw new Error(`HostAPI.http.request: path must be relative (got "${path}")`);
  const res = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

export function createHostApi(pluginId: string, hostVersion: string): HostAPI {
  const subscriptions: Disposable[] = [];
  const owner = { owner: pluginId };
  const host: HostAPI = {
    version: hostVersion,
    api: { ui: HOST_API_VERSION },
    pluginId,
    libs: { react: React, reactDom: ReactDOM, router: ReactRouter, zustand: Zustand, i18next, icons: Icons },
    registries: {
      pages: PAGES,
      sidebar: SIDEBAR,
      panels: PANELS,
      settingsSections: SETTINGS_SECTIONS,
      commands: COMMANDS,
      themes: THEMES,
      messageRenderers: MESSAGE_RENDERERS,
      timelineEvents: TIMELINE_EVENTS,
      conversationKinds: CONVERSATION_KINDS,
      chatHeaderActions: CHAT_HEADER_ACTIONS,
      composerExtensions: COMPOSER_EXTENSIONS,
      messageActions: MESSAGE_ACTIONS,
      sidebarSections: SIDEBAR_SECTIONS,
      agentCardBadges: AGENT_CARD_BADGES,
      topBarItems: TOP_BAR_ITEMS,
    },
    register<T>(registry: Registry<T>, id: string, value: T): Disposable {
      // A plugin may replace the lazy gate the loader registered under its own id.
      const dispose = registry.register(id, value, { ...owner, replace: registry.has(id) && registryOwnerIs(registry, id, pluginId) });
      const d = { dispose };
      subscriptions.push(d);
      return d;
    },
    http: { request: requestJson, prefix: `/api/x/${pluginId}` },
    i18n: {
      addResourceBundle: (lng, resources) => addPluginBundle(pluginId, lng, resources),
      t: (key, options) => pluginT(pluginId, key, options),
    },
    log: { error: (error, context) => reportUiError(error instanceof Error ? error : new Error(String(error)), { kind: 'render', source: pluginId, context }) },
    subscriptions,
    dispose() {
      for (const s of subscriptions.splice(0).reverse()) {
        try {
          s.dispose();
        } catch (e) {
          reportUiError(e instanceof Error ? e : new Error(String(e)), { kind: 'render', source: pluginId, context: 'dispose' });
        }
      }
    },
  };
  return host;
}

function registryOwnerIs<T>(registry: Registry<T>, id: string, owner: string): boolean {
  return registry.list().some((e) => e.id === id && e.owner === owner);
}
