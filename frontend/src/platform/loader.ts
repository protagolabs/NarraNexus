/**
 * @file_name: loader.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Loads user plugins into the shell: declarative metadata first (gates in the registries), code on activation, with SRI.
 *
 * `loadPlugins()` asks the factory API which plugins are enabled and
 * loaded, registers a lazy gate for every page/panel/command their manifest
 * declares, and arms activation. Activation fetches the plugin's `plugin.js`
 * (desktop: `plugin://<id>/…`; web: the factory's asset route), verifies
 * the manifest's `integrity` (sha256 SRI) when present, imports it as an
 * ESM module from a blob URL, and calls `plugin.activate(host)`. The blob
 * URL is recorded for error attribution. Nothing here throws to the
 * caller: a broken plugin costs one error report, never the shell.
 */

import { isTauri } from '@/lib/tauri';
import { getApiBaseUrl } from '@/stores/runtimeStore';
import { getAuthHeaders } from '@/lib/authHeaders';
import { fireActivation, registerActivation } from './activation';
import { attributeChunkUrl, reportUiError } from './errorSink';
import { makeActionGate } from './actionGate';
import { makePageGate, makePanelGate, makeRendererGate, makeSlotGate, makeTimelineGate } from './gates';
import { createHostApi, exposeHostGlobals, type HostAPI } from './host';
import {
  AGENT_CARD_BADGES,
  CHANNELS,
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
  type SlotActionDef,
  type SlotComponentDef,
} from './registries';

export interface FactoryPluginRow {
  id: string;
  version: string;
  enabled: boolean;
  loaded: boolean;
  state: string;
  frontend: null | {
    entry: string;
    locales?: string;
    integrity?: string;
    ui?: {
      pages?: { id: string; path: string; layout?: 'app' | 'top'; guard?: 'protected' | 'public' | 'open' }[];
      panels?: { id: string; label?: string }[];
      commands?: { id: string; label: string; hint?: string }[];
      themes?: string[];
      /** Kinds `when: conversationKind:<k>` may name; registered up front. */
      conversationKinds?: { id: string; label?: string }[];
      /** A gate renders until the plugin registers the real renderer under the same id. */
      messageRenderers?: { id: string; role?: 'user' | 'assistant'; contentPrefix?: string }[];
      timelineEvents?: { id: string; type: string }[];
      /** Slot-point entries declared up front (component slots mount a silent gate; action slots a labelled one). */
      slots?: { id: string; point: SlotPoint; label?: string; when?: string[]; order?: number }[];
    };
  };
  activation_events?: string[];
}

export interface FactoryBuiltinRow {
  id: string;
  enabled: boolean;
  protected: boolean;
}

/**
 * Remove every shell registration owned by a disabled builtin plugin (its pages,
 * sidebar rows, panels, commands…). `platform/builtin.ts` tags feature-level
 * builtins with their plugin id as owner, so the whole UI row goes with one call.
 */
export function disableBuiltinUi(pluginId: string): string[] {
  const removed: string[] = [];
  for (const reg of SHELL_REGISTRIES) removed.push(...reg.removeOwner(pluginId).map((id) => `${reg.kind}:${id}`));
  return removed;
}

export type SlotPoint = 'chatHeaderActions' | 'composerExtensions' | 'messageActions' | 'sidebarSections' | 'agentCardBadges' | 'topBarItems';

const COMPONENT_SLOTS: Record<string, Registry<SlotComponentDef>> = {
  composerExtensions: COMPOSER_EXTENSIONS,
  sidebarSections: SIDEBAR_SECTIONS,
  agentCardBadges: AGENT_CARD_BADGES,
  topBarItems: TOP_BAR_ITEMS,
};
const ACTION_SLOTS: Record<string, Registry<SlotActionDef>> = {
  chatHeaderActions: CHAT_HEADER_ACTIONS,
  messageActions: MESSAGE_ACTIONS,
};

/** Every shell registry a disabled builtin's UI row is removed from (`disableBuiltinUi`). */
const SHELL_REGISTRIES = [PAGES, PANELS, COMMANDS, SIDEBAR, SETTINGS_SECTIONS, THEMES, MESSAGE_RENDERERS, TIMELINE_EVENTS, CONVERSATION_KINDS, CHANNELS, ...Object.values(COMPONENT_SLOTS), ...Object.values(ACTION_SLOTS)] as const;

export interface PluginModule {
  plugin?: { activate(host: HostAPI): void | Promise<void>; deactivate?(host: HostAPI): void | Promise<void> };
  activate?(host: HostAPI): void | Promise<void>;
}

export interface LoaderDeps {
  fetchImpl?: typeof fetch;
  importImpl?: (url: string) => Promise<PluginModule>;
  /** SHA-256 over the bundle bytes; defaults to WebCrypto (tests inject node:crypto). */
  digestImpl?: (bytes: ArrayBuffer) => Promise<ArrayBuffer>;
  hostVersion?: string;
}

const hosts = new Map<string, HostAPI>();

export function assetUrl(pluginId: string, entry: string): string {
  const clean = entry.replace(/^\/+/, '').replace(/^frontend\/dist\//, '');
  if (isTauri()) return `plugin://${pluginId}/${clean}`;
  return `${getApiBaseUrl()}/api/plugin-factory/${encodeURIComponent(pluginId)}/assets/${clean}`;
}

const defaultDigest = (bytes: ArrayBuffer) => crypto.subtle.digest('SHA-256', bytes);

async function sha256Base64(bytes: ArrayBuffer, digestImpl: LoaderDeps['digestImpl']): Promise<string> {
  const digest = await (digestImpl ?? defaultDigest)(bytes);
  let binary = '';
  for (const b of new Uint8Array(digest)) binary += String.fromCharCode(b);
  return `sha256-${btoa(binary)}`;
}

async function fetchVerified(url: string, integrity: string | undefined, deps: LoaderDeps): Promise<string> {
  const fetchImpl = deps.fetchImpl ?? fetch;
  const res = await fetchImpl(url, { headers: isTauri() ? {} : getAuthHeaders() });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const bytes = await res.arrayBuffer();
  if (integrity) {
    const actual = await sha256Base64(bytes, deps.digestImpl);
    if (actual !== integrity) throw new Error(`integrity mismatch for ${url} (expected ${integrity}, got ${actual})`);
  }
  return URL.createObjectURL(new Blob([bytes], { type: 'text/javascript' }));
}

function defaultImport(url: string): Promise<PluginModule> {
  return import(/* @vite-ignore */ url) as Promise<PluginModule>;
}

export function registerDeclaredUi(row: FactoryPluginRow): string[] {
  const events: string[] = [...(row.activation_events ?? [])];
  const ui = row.frontend?.ui;
  const owner = { owner: row.id };
  for (const page of ui?.pages ?? []) {
    if (!PAGES.has(page.id)) {
      PAGES.register(page.id, { path: page.path, element: makePageGate(row.id, page.id), guard: page.guard ?? 'protected', layout: page.layout ?? 'app' }, owner);
    }
    events.push(`onPage:${page.id}`);
  }
  for (const panel of ui?.panels ?? []) {
    if (!PANELS.has(panel.id)) PANELS.register(panel.id, { component: makePanelGate(row.id, panel.id) }, owner);
    events.push(`onPanel:${panel.id}`);
  }
  for (const kind of ui?.conversationKinds ?? []) {
    if (!CONVERSATION_KINDS.has(kind.id)) CONVERSATION_KINDS.register(kind.id, { labelKey: kind.label ?? kind.id }, owner);
  }
  for (const r of ui?.messageRenderers ?? []) {
    if (!MESSAGE_RENDERERS.has(r.id)) MESSAGE_RENDERERS.register(r.id, makeRendererGate(row.id, r.id, { role: r.role, contentPrefix: r.contentPrefix }), owner);
    events.push(`onRenderer:${r.id}`);
  }
  for (const te of ui?.timelineEvents ?? []) {
    if (!TIMELINE_EVENTS.has(te.type)) TIMELINE_EVENTS.register(te.type, { component: makeTimelineGate(row.id, te.id, te.type) }, owner);
    events.push(`onTimelineEvent:${te.id}`);
  }
  for (const slot of ui?.slots ?? []) {
    const component = COMPONENT_SLOTS[slot.point];
    const action = ACTION_SLOTS[slot.point];
    if (component && !component.has(slot.id)) {
      component.register(slot.id, { component: makeSlotGate(row.id, slot.id), when: slot.when, order: slot.order }, owner);
    } else if (action && !action.has(slot.id)) {
      action.register(slot.id, makeActionGate(row.id, slot.id, action, slot.label ?? slot.id, slot.when, slot.order), owner);
    }
    events.push(`onSlot:${slot.id}`);
  }
  for (const cmd of ui?.commands ?? []) {
    if (!COMMANDS.has(cmd.id)) {
      COMMANDS.register(
        cmd.id,
        {
          label: cmd.label,
          hint: cmd.hint,
          run: async () => {
            await fireActivation(`onCommand:${cmd.id}`);
            const real = COMMANDS.list().find((e) => e.id === cmd.id && e.owner === row.id);
            // the plugin replaced the gate with its own command during activate()
            if (real && real.value.run !== undefined && real.value.label !== cmd.label) await real.value.run();
          },
        },
        owner,
      );
    }
    events.push(`onCommand:${cmd.id}`);
  }
  return Array.from(new Set(events));
}

export async function activatePlugin(row: FactoryPluginRow, deps: LoaderDeps = {}): Promise<HostAPI> {
  const importImpl = deps.importImpl ?? defaultImport;
  if (!row.frontend) throw new Error(`${row.id}: no frontend entry`);
  const url = assetUrl(row.id, row.frontend.entry);
  const blobUrl = await fetchVerified(url, row.frontend.integrity || undefined, deps);
  attributeChunkUrl(blobUrl, row.id);
  const mod = await importImpl(blobUrl);
  const activate = mod.plugin?.activate ?? mod.activate;
  if (typeof activate !== 'function') throw new Error(`${row.id}: bundle exports no activate(host)`);
  const host = createHostApi(row.id, deps.hostVersion ?? '');
  await activate.call(mod.plugin ?? mod, host);
  hosts.set(row.id, host);
  return host;
}

export function deactivatePlugin(pluginId: string): boolean {
  const host = hosts.get(pluginId);
  if (!host) return false;
  host.dispose();
  hosts.delete(pluginId);
  return true;
}

export function activeHosts(): string[] {
  return [...hosts.keys()];
}

/** Boot-time entry: register metadata for every loaded plugin, then fire onStartup. */
export async function loadPlugins(deps: LoaderDeps = {}): Promise<FactoryPluginRow[]> {
  exposeHostGlobals();
  const fetchImpl = deps.fetchImpl ?? fetch;
  let rows: FactoryPluginRow[] = [];
  try {
    const res = await fetchImpl(`${getApiBaseUrl()}/api/plugin-factory`, { headers: getAuthHeaders() });
    if (!res.ok) return [];
    const body = (await res.json()) as { data?: { plugins?: FactoryPluginRow[]; builtins?: FactoryBuiltinRow[] } };
    for (const b of body.data?.builtins ?? []) if (!b.enabled && !b.protected) disableBuiltinUi(b.id);
    rows = (body.data?.plugins ?? []).filter((r) => r.enabled && r.loaded && r.frontend);
  } catch (e) {
    reportUiError(e instanceof Error ? e : new Error(String(e)), { kind: 'chunk', source: 'shell', context: 'loadPlugins' });
    return [];
  }
  for (const row of rows) {
    try {
      const events = registerDeclaredUi(row);
      registerActivation(row.id, events.length ? events : ['onStartup'], async () => {
        await activatePlugin(row, deps);
      });
    } catch (e) {
      reportUiError(e instanceof Error ? e : new Error(String(e)), { kind: 'chunk', source: row.id, context: 'registerDeclaredUi' });
    }
  }
  await fireActivation('onStartup');
  return rows;
}
