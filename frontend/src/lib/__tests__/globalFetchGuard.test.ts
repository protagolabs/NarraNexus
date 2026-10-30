/**
 * @file_name: globalFetchGuard.test.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: installGlobalFetchGuard routes the page's global fetch through the request-loop guard, so plugin bundles that call fetch directly are covered.
 *
 * Plugin bundles run as modules in this realm and are invisible to the
 * no-raw-fetch source scan; before this, a plugin's render loop on its own
 * endpoint had no brake at all. Requests to other origins pass straight
 * through (the edge never sees them, so their failures must not spend the
 * page's budget), and the session probe keeps the unguarded path.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { RequestLoopBlockedError } from '../apiError';
import {
  __resetRequestLoopGuardForTests,
  __uninstallGlobalFetchGuardForTests,
  installGlobalFetchGuard,
} from '../guardedFetch';
import { FAIL_MAX, LOOP_BLOCK_DELAY_MS, LOOP_MAX_PER_WINDOW, PAGE_FAIL_MAX } from '../requestLoopGuard';
import { confirmSessionDeath, resetSessionGuard } from '../sessionGuard';
import { useRuntimeStore } from '@/stores/runtimeStore';
import { useConfigStore } from '@/stores/configStore';

let native: ReturnType<typeof vi.fn>;

beforeEach(() => {
  useRuntimeStore.setState({ mode: 'cloud-web' }); // behind the edge
  __resetRequestLoopGuardForTests();
  native = vi.fn(async (input: RequestInfo | URL) =>
    new Response('{}', { status: String(input).includes('/gone/') ? 404 : 200 }));
  globalThis.fetch = native as unknown as typeof fetch;
  installGlobalFetchGuard();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  __uninstallGlobalFetchGuardForTests();
  useRuntimeStore.setState({ mode: null });
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('installGlobalFetchGuard', () => {
  it("a plugin's direct fetch loop on its own backend is braked", async () => {
    vi.useFakeTimers();
    for (let i = 0; i < LOOP_MAX_PER_WINDOW; i++) await fetch('/api/plugin/status');
    const refused = fetch('/api/plugin/status').catch((e) => e);
    await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS);
    expect(await refused).toBeInstanceOf(RequestLoopBlockedError);
    expect(native).toHaveBeenCalledTimes(LOOP_MAX_PER_WINDOW);
  });

  it("a plugin URL that keeps failing is blocked", async () => {
    vi.useFakeTimers();
    for (let i = 0; i < FAIL_MAX; i++) {
      await fetch('/api/plugin/gone/x');
      await vi.advanceTimersByTimeAsync(3_000);
    }
    const refused = fetch('/api/plugin/gone/x').catch((e) => e);
    await vi.advanceTimersByTimeAsync(LOOP_BLOCK_DELAY_MS);
    expect(await refused).toBeInstanceOf(RequestLoopBlockedError);
  });

  it("a plugin's failures spend the same page budget as the host's reads", async () => {
    for (let i = 0; i < PAGE_FAIL_MAX; i++) await fetch(`/api/plugin/gone/${i}`);
    // The host's own unproven read is now paused too: one budget per page.
    const { guardedFetch } = await import('../guardedFetch');
    await expect(guardedFetch('/api/host/never/seen')).rejects.toBeInstanceOf(RequestLoopBlockedError);
  });

  it('cross-realm URL objects are keyed by their href, not lumped together', async () => {
    // A URL from another realm is not `instanceof URL`; keying it as undefined
    // would make distinct reads look like one URL in a loop.
    const origin = window.location.origin;
    for (let i = 0; i < LOOP_MAX_PER_WINDOW + 5; i++) {
      const foreign = { toString: () => `${origin}/api/plugin/item/${i}` } as unknown as URL;
      await fetch(foreign);
    }
    expect(native).toHaveBeenCalledTimes(LOOP_MAX_PER_WINDOW + 5);
  });

  it('requests to other origins pass straight through and spend nothing', async () => {
    for (let i = 0; i < PAGE_FAIL_MAX * 2; i++) await fetch(`https://telemetry.example.com/gone/${i}`);
    expect(native).toHaveBeenCalledTimes(PAGE_FAIL_MAX * 2);
    // The page budget is untouched: an own-backend first read still goes out.
    expect((await fetch('/api/never/seen')).status).toBe(200);
  });

  it('writes are never refused, through the global fetch either', async () => {
    for (let i = 0; i < LOOP_MAX_PER_WINDOW * 2; i++) await fetch('/api/plugin/save', { method: 'POST' });
    expect(native).toHaveBeenCalledTimes(LOOP_MAX_PER_WINDOW * 2);
  });

  it('the session probe keeps the unguarded path even when reads are paused', async () => {
    for (let i = 0; i < PAGE_FAIL_MAX; i++) await fetch(`/api/gone/${i}`);
    await expect(fetch('/api/never/seen')).rejects.toBeInstanceOf(RequestLoopBlockedError);
    resetSessionGuard();
    native.mockClear();
    useConfigStore.setState({ userId: 'u1', token: 't', isLoggedIn: true });
    await confirmSessionDeath({ endpoint: '/api/x', code: null });
    expect(native.mock.calls.some(([u]) => String(u).includes('/api/auth/session'))).toBe(true);
  });

  it('installing twice keeps a single wrapper over the real fetch', async () => {
    const wrapped = globalThis.fetch;
    installGlobalFetchGuard();
    expect(globalThis.fetch).toBe(wrapped);
    await fetch('/api/once');
    expect(native).toHaveBeenCalledTimes(1);
  });

  it('main.tsx installs it before any plugin can load', () => {
    const main = readFileSync(join(process.cwd(), 'src/main.tsx'), 'utf8');
    const install = main.search(/^installGlobalFetchGuard\(\)/m);
    const boot = main.search(/^void bootPlugins\(\)/m);
    expect(install).toBeGreaterThan(-1);
    expect(install).toBeLessThan(boot);
  });
});
