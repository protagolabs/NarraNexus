/**
 * @file_name: loader.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The loader registers gates from manifest metadata, activates a plugin through fetch+SRI+import, and the gate renders the plugin's page after activation.
 */
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fireActivation, resetActivation } from '@/platform/activation';
import { resetErrorSink } from '@/platform/errorSink';
import { activatePlugin, activeHosts, deactivatePlugin, loadPlugins, registerDeclaredUi, type FactoryPluginRow, type PluginModule } from '@/platform/loader';
import { COMMANDS, PAGES, PANELS } from '@/platform/registries';

const ROW: FactoryPluginRow = {
  id: 'acme.weather',
  version: '1.0.0',
  enabled: true,
  loaded: true,
  state: 'enabled',
  frontend: {
    entry: 'plugin.js',
    ui: {
      pages: [{ id: 'acme.weather.home', path: 'x/weather' }],
      panels: [{ id: 'acme.weather.panel' }],
      commands: [{ id: 'acme.weather.refresh', label: 'Refresh weather' }],
    },
  },
  activation_events: [],
};

import { createHash } from 'node:crypto';

const digestImpl = async (bytes: ArrayBuffer): Promise<ArrayBuffer> => {
  const out = createHash('sha256').update(new Uint8Array(bytes)).digest();
  return out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength) as ArrayBuffer;
};

async function sri(text: string): Promise<string> {
  const digest = await digestImpl(new TextEncoder().encode(text).buffer as ArrayBuffer);
  return 'sha256-' + btoa(String.fromCharCode(...new Uint8Array(digest)));
}

function fakeFetch(body: string, status = 200): typeof fetch {
  return vi.fn(async () => new Response(body, { status })) as unknown as typeof fetch;
}

afterEach(() => {
  resetActivation();
  resetErrorSink();
  for (const id of ['acme.weather.home', 'acme.weather.panel', 'acme.weather.refresh']) {
    for (const reg of [PAGES, PANELS, COMMANDS]) {
      const entry = reg.list().find((e) => e.id === id);
      if (entry) (reg as { register: typeof PAGES.register }).register(id, entry.value as never, { owner: entry.owner, replace: true })();
    }
  }
  deactivatePlugin('acme.weather');
});

describe('loader', () => {
  it('registers gates for declared pages/panels/commands under the plugin owner', () => {
    const events = registerDeclaredUi(ROW);
    expect(events).toEqual(['onPage:acme.weather.home', 'onPanel:acme.weather.panel', 'onCommand:acme.weather.refresh']);
    expect(PAGES.list().find((e) => e.id === 'acme.weather.home')?.owner).toBe('acme.weather');
    expect(PAGES.get('acme.weather.home')?.path).toBe('x/weather');
    expect(PANELS.has('acme.weather.panel') && COMMANDS.has('acme.weather.refresh')).toBe(true);
  });

  it('activates through fetch, verifies SRI, imports and calls activate(host)', async () => {
    const source = 'export const plugin = {}';
    const activateSpy = vi.fn();
    const mod: PluginModule = { plugin: { activate: activateSpy } };
    const row = { ...ROW, frontend: { ...ROW.frontend!, integrity: await sri(source) } };
    const host = await activatePlugin(row, { fetchImpl: fakeFetch(source), importImpl: async () => mod, hostVersion: '1.19.0', digestImpl });
    expect(activateSpy).toHaveBeenCalledWith(host);
    expect(host.pluginId).toBe('acme.weather');
    expect(host.http.prefix).toBe('/api/x/acme.weather');
    expect(activeHosts()).toEqual(['acme.weather']);
    expect(deactivatePlugin('acme.weather')).toBe(true);
    expect(activeHosts()).toEqual([]);
  });

  it('refuses a bundle whose integrity does not match', async () => {
    const row = { ...ROW, frontend: { ...ROW.frontend!, integrity: 'sha256-AAAA' } };
    await expect(activatePlugin(row, { fetchImpl: fakeFetch('x'), importImpl: async () => ({}), digestImpl })).rejects.toThrow(/integrity mismatch/);
    await expect(activatePlugin(ROW, { fetchImpl: fakeFetch('x', 404), importImpl: async () => ({}) })).rejects.toThrow(/HTTP 404/);
    await expect(activatePlugin(ROW, { fetchImpl: fakeFetch('x'), importImpl: async () => ({}) })).rejects.toThrow(/no activate/);
  });

  it('page gate shows loading, then the page the plugin registered during activate', async () => {
    const importImpl = async (): Promise<PluginModule> => ({
      plugin: {
        activate(host) {
          host.register(host.registries.pages, 'acme.weather.home', { path: 'x/weather', element: () => <p>weather page</p>, guard: 'protected', layout: 'app' });
        },
      },
    });
    const list = JSON.stringify({ data: { plugins: [ROW] } });
    const fetchImpl = vi.fn(async (url: string) => new Response(url.endsWith('/api/plugin-factory') ? list : 'export {}', { status: 200 })) as unknown as typeof fetch;
    const rows = await loadPlugins({ fetchImpl, importImpl, digestImpl });
    expect(rows.map((r) => r.id)).toEqual(['acme.weather']);
    const Gate = PAGES.get('acme.weather.home')!.element as React.ComponentType;
    render(<Gate />);
    expect(screen.getByText(/Loading plugin acme.weather/)).toBeInTheDocument();
    await act(async () => {
      await fireActivation('onPage:acme.weather.home');
    });
    expect(await screen.findByText('weather page')).toBeInTheDocument();
    expect(PAGES.list().find((e) => e.id === 'acme.weather.home')?.owner).toBe('acme.weather');
  });

  it('a failing factory request loads nothing and never throws', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error('offline');
    }) as unknown as typeof fetch;
    expect(await loadPlugins({ fetchImpl })).toEqual([]);
  });
});
