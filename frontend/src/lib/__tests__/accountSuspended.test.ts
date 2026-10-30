/**
 * @file_name: accountSuspended.test.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: Every request path that can meet the backend's 403 account_suspended raises the sign-out event.
 *
 * A suspended account gets 403 account_suspended on every request. If any path
 * kept polling without noticing, its 403s would count toward the edge
 * monitor's IP ban. Detection therefore sits in guardedFetch (every HTTP call
 * in the app goes through it, reads and writes alike) and in the session
 * probe (where the WebSocket's AuthError path lands).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { __resetRequestLoopGuardForTests, guardedFetch } from '../guardedFetch';
import { confirmSessionDeath, resetSessionGuard } from '../sessionGuard';
import { useConfigStore } from '@/stores/configStore';

const suspendedBody = () =>
  new Response(JSON.stringify({ detail: 'Account is not available', code: 'account_suspended' }), { status: 403 });

let events: Event[];
const on = (e: Event) => events.push(e);
const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  __resetRequestLoopGuardForTests();
  events = [];
  window.addEventListener('narranexus:account-suspended', on);
});
afterEach(() => {
  window.removeEventListener('narranexus:account-suspended', on);
  vi.restoreAllMocks();
});

describe('account_suspended detection', () => {
  it('a read through guardedFetch (not api.request) raises the event, and the caller still reads the body', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => suspendedBody());
    const res = await guardedFetch('/api/artifacts/list');
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ code: 'account_suspended' }); // body not consumed
    await flush();
    expect(events).toHaveLength(1);
  });

  it('a write raises it too', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => suspendedBody());
    await guardedFetch('/api/agents/x/rename', { method: 'POST' });
    await flush();
    expect(events).toHaveLength(1);
  });

  it("an ordinary 403 or a non-JSON 403 raises nothing", async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Forbidden' }), { status: 403 }))
      .mockResolvedValueOnce(new Response('<html>nope</html>', { status: 403 }));
    await guardedFetch('/api/a');
    await guardedFetch('/api/b');
    await flush();
    expect(events).toHaveLength(0);
  });

  it('the session probe (where the WebSocket AuthError lands) raises it on 403 account_suspended', async () => {
    resetSessionGuard();
    useConfigStore.getState().login('alice', 'jwt');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => suspendedBody());
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    await confirmSessionDeath({ endpoint: 'ws', code: 'account_suspended' });
    await flush();
    expect(events.length).toBeGreaterThanOrEqual(1);
    useConfigStore.getState().logout();
  });
});
