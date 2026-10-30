/**
 * @file_name: flag.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The mock-mode switch on its own, so the eager bundle can ask "is mock on?" without carrying the fixtures.
 *
 * Enable with `?mock=1` in the URL (persists to localStorage), disable with
 * `?mock=0` or the dev banner. The mock implementations (`./index.ts`) are
 * loaded by main.tsx only when this is on, before the first render.
 */

const STORAGE_KEY = 'narranexus.mock.enabled';

/** Read ?mock=0|1 from URL; persist to localStorage. Returns final state. */
function resolveMockFlag(): boolean {
  if (typeof window === 'undefined') return false;
  const url = new URL(window.location.href);
  const q = url.searchParams.get('mock');
  if (q === '1' || q === 'true') {
    localStorage.setItem(STORAGE_KEY, '1');
    return true;
  }
  if (q === '0' || q === 'false') {
    localStorage.removeItem(STORAGE_KEY);
    return false;
  }
  return localStorage.getItem(STORAGE_KEY) === '1';
}

export const MOCK_ENABLED = resolveMockFlag();

export function setMockEnabled(enabled: boolean) {
  if (enabled) localStorage.setItem(STORAGE_KEY, '1');
  else localStorage.removeItem(STORAGE_KEY);
  window.location.reload();
}
