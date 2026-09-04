/**
 * @file_name: host.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: HostAPI registers as the plugin, tracks disposals, unwinds on dispose, and exposes the framework globals once.
 */
import { afterEach, describe, expect, it } from 'vitest';

import { createHostApi, exposeHostGlobals } from '@/platform/host';
import { COMMANDS } from '@/platform/registries';

afterEach(() => {
  for (const e of COMMANDS.list().filter((x) => x.owner === 'acme.t')) COMMANDS.register(e.id, e.value, { owner: e.owner, replace: true })();
});

describe('HostAPI', () => {
  it('registers under the plugin id and disposes everything on dispose()', () => {
    const host = createHostApi('acme.t', '1.19.0');
    host.register(host.registries.commands, 'acme.t.cmd', { label: 'x', run: () => undefined });
    expect(COMMANDS.list().find((e) => e.id === 'acme.t.cmd')?.owner).toBe('acme.t');
    expect(host.subscriptions).toHaveLength(1);
    host.dispose();
    expect(COMMANDS.has('acme.t.cmd')).toBe(false);
    expect(host.subscriptions).toHaveLength(0);
  });

  it('may replace its own gate entry but not another owner\'s entry', () => {
    COMMANDS.register('acme.t.cmd', { label: 'gate', run: () => undefined }, { owner: 'acme.t' });
    const host = createHostApi('acme.t', '1');
    host.register(host.registries.commands, 'acme.t.cmd', { label: 'real', run: () => undefined });
    expect(COMMANDS.get('acme.t.cmd')?.label).toBe('real');
    COMMANDS.register('other.cmd', { label: 'o', run: () => undefined }, { owner: 'other' });
    expect(() => host.register(host.registries.commands, 'other.cmd', { label: 'steal', run: () => undefined })).toThrow(/already registered/);
    COMMANDS.register('other.cmd', { label: 'o', run: () => undefined }, { owner: 'other', replace: true })();
  });

  it('exposes the host globals once and refuses non-relative http paths', async () => {
    exposeHostGlobals();
    const first = window.__narranexus_host__;
    exposeHostGlobals();
    expect(window.__narranexus_host__).toBe(first);
    expect(first?.react).toBeDefined();
    const host = createHostApi('acme.t', '1');
    await expect(host.http.request('https://evil')).rejects.toThrow(/relative/);
  });
});
