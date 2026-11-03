/**
 * @file_name: buildWatch.test.ts
 * @description: The deployed-build check behind the cloud "new version" banner.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const guardedFetch = vi.fn();
vi.mock('../guardedFetch', () => ({ guardedFetch: (...args: unknown[]) => guardedFetch(...args) }));

import { BUILD_CHECK_INTERVAL_MS, fetchDeployedBuildId, startBuildWatch } from '../buildWatch';

// Minimal Response stand-ins: json() settles in one microtask, so `flush`
// is enough to finish a check (a real Response parses over several ticks).
const json = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});
const html = () => ({
  ok: true,
  status: 200,
  json: async () => {
    throw new SyntaxError('Unexpected token < in JSON');
  },
});
const flush = async () => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  vi.useFakeTimers();
  guardedFetch.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('fetchDeployedBuildId', () => {
  it('reads buildId from /build.json, bypassing caches', async () => {
    guardedFetch.mockResolvedValue(json({ buildId: 'b2' }));
    await expect(fetchDeployedBuildId()).resolves.toBe('b2');
    const [url, init] = guardedFetch.mock.calls[0];
    expect(String(url)).toMatch(/^\/build\.json\?t=\d+$/);
    expect(init).toMatchObject({ cache: 'no-store' });
    expect(init.signal).toBeInstanceOf(AbortSignal); // a hung read cannot wedge the watch
  });

  it('is null for the SPA index.html fallback of a deploy without build.json', async () => {
    guardedFetch.mockResolvedValue(html());
    await expect(fetchDeployedBuildId()).resolves.toBeNull();
  });

  it('is null on HTTP errors, network errors and malformed bodies', async () => {
    guardedFetch.mockResolvedValueOnce(json({ buildId: 'b2' }, 404));
    await expect(fetchDeployedBuildId()).resolves.toBeNull();
    guardedFetch.mockRejectedValueOnce(new TypeError('offline'));
    await expect(fetchDeployedBuildId()).resolves.toBeNull();
    guardedFetch.mockResolvedValueOnce(json({ buildId: 42 }));
    await expect(fetchDeployedBuildId()).resolves.toBeNull();
    guardedFetch.mockResolvedValueOnce(json({ buildId: '' }));
    await expect(fetchDeployedBuildId()).resolves.toBeNull();
  });
});

describe('startBuildWatch', () => {
  it('reports a different deployed build once, then stops checking', async () => {
    guardedFetch.mockResolvedValue(json({ buildId: 'new' }));
    const onNew = vi.fn();
    startBuildWatch(onNew, 'old');
    await flush();
    expect(onNew).toHaveBeenCalledWith('new');
    vi.advanceTimersByTime(BUILD_CHECK_INTERVAL_MS * 3);
    setVisibility('visible');
    await flush();
    expect(guardedFetch).toHaveBeenCalledTimes(1);
    expect(onNew).toHaveBeenCalledTimes(1);
  });

  it('stays quiet while the deployed build matches, and re-checks on the interval', async () => {
    guardedFetch.mockResolvedValue(json({ buildId: 'same' }));
    const onNew = vi.fn();
    const stop = startBuildWatch(onNew, 'same');
    await flush();
    vi.advanceTimersByTime(BUILD_CHECK_INTERVAL_MS);
    await flush();
    expect(guardedFetch).toHaveBeenCalledTimes(2);
    expect(onNew).not.toHaveBeenCalled();
    stop();
  });

  it('re-checks when a background tab becomes visible again', async () => {
    guardedFetch.mockResolvedValue(json({ buildId: 'same' }));
    const onNew = vi.fn();
    const stop = startBuildWatch(onNew, 'same');
    await flush();
    guardedFetch.mockResolvedValue(json({ buildId: 'new' }));
    setVisibility('hidden');
    await flush();
    expect(onNew).not.toHaveBeenCalled();
    setVisibility('visible');
    await flush();
    expect(onNew).toHaveBeenCalledWith('new');
    stop();
  });

  it('never reports when the deployed build is unknown', async () => {
    guardedFetch.mockResolvedValue(html());
    const onNew = vi.fn();
    const stop = startBuildWatch(onNew, 'old');
    await flush();
    vi.advanceTimersByTime(BUILD_CHECK_INTERVAL_MS);
    await flush();
    expect(onNew).not.toHaveBeenCalled();
    stop();
  });

  it('stop() ends the interval and the visibility listener', async () => {
    guardedFetch.mockResolvedValue(json({ buildId: 'same' }));
    const stop = startBuildWatch(vi.fn(), 'same');
    await flush();
    stop();
    vi.advanceTimersByTime(BUILD_CHECK_INTERVAL_MS * 2);
    setVisibility('visible');
    await flush();
    expect(guardedFetch).toHaveBeenCalledTimes(1);
  });

  it('drops a result that lands after stop()', async () => {
    let resolveRead: (v: unknown) => void = () => {};
    guardedFetch.mockReturnValue(new Promise((r) => { resolveRead = r; }));
    const onNew = vi.fn();
    const stop = startBuildWatch(onNew, 'old');
    stop();
    resolveRead(json({ buildId: 'new' }));
    await flush();
    expect(onNew).not.toHaveBeenCalled();
  });

  it('does not stack reads while one is in flight', async () => {
    guardedFetch.mockReturnValue(new Promise(() => {}));
    const stop = startBuildWatch(vi.fn(), 'same');
    setVisibility('visible');
    vi.advanceTimersByTime(BUILD_CHECK_INTERVAL_MS);
    await flush();
    expect(guardedFetch).toHaveBeenCalledTimes(1);
    stop();
  });
});
