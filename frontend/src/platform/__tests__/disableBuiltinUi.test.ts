/**
 * @file_name: disableBuiltinUi.test.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: A builtin reported disabled by the factory drops its whole UI row (teams pages) at boot; protected and enabled builtins keep theirs.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import '@/platform/builtin';
import { disableBuiltinUi, loadPlugins } from '@/platform/loader';
import { PAGES, SIDEBAR } from '@/platform/registries';

vi.mock('@/stores/runtimeStore', () => ({ getApiBaseUrl: () => 'http://api' }));
vi.mock('@/lib/authHeaders', () => ({ getAuthHeaders: () => ({}) }));

const TEAM_PAGES = ['teams-new', 'team-detail', 'team-chat'];

afterEach(() => vi.restoreAllMocks());

describe('disableBuiltinUi', () => {
  it('builtin.ts tags the teams pages with the builtin.teams owner', () => {
    for (const id of TEAM_PAGES) expect(PAGES.list().find((e) => e.id === id)?.owner).toBe('builtin.teams');
    expect(PAGES.list().find((e) => e.id === 'settings')?.owner).toBe('builtin.ui');
  });

  it('loadPlugins removes the UI row of a disabled builtin and leaves protected/enabled ones alone', async () => {
    const before = PAGES.ids().length;
    const body = JSON.stringify({
      data: {
        plugins: [],
        builtins: [
          { id: 'builtin.nexus_plugins_module', enabled: false, protected: true },
          { id: 'builtin.chat', enabled: true, protected: false },
          { id: 'builtin.teams', enabled: false, protected: false },
        ],
      },
    });
    const fetchImpl = vi.fn(async () => new Response(body, { status: 200 })) as unknown as typeof fetch;
    await loadPlugins({ fetchImpl });
    for (const id of TEAM_PAGES) expect(PAGES.has(id)).toBe(false);
    expect(PAGES.ids().length).toBe(before - TEAM_PAGES.length);
    expect(SIDEBAR.ids()).toContain('settings');
    expect(disableBuiltinUi('builtin.teams')).toEqual([]); // idempotent
  });
});
