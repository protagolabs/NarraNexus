/**
 * @file_name: no-raw-fetch.test.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: Every network call goes through `guardedFetch` — a raw `fetch(` bypasses the request-loop guard.
 *
 * On 2026-10-30 a render loop in the Channels panel flooded the edge and got a
 * real user's IP banned and account suspended. The guard in `guardedFetch.ts`
 * is the backstop for the next such loop, but only for calls that pass through
 * it: a panel that calls `fetch` directly is exactly as unprotected as before.
 * A per-component test cannot catch that; only a scan over the source can, and
 * only a scan covers files not written yet (same reasoning as
 * `no-unauthenticated-api-fetch.test.ts`).
 */
import { describe, expect, test } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(process.cwd(), 'src');

/** Files allowed to call `fetch` directly, each for a stated reason. */
const ALLOWED = new Set([
  // The guard's own implementation.
  'lib/guardedFetch.ts',
  // The session-death referee: a refusal here would read as a dead session and
  // log the user out. It runs only after a real 401, so it cannot loop on its own.
  'lib/sessionGuard.ts',
]);

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '__tests__' || name === 'dist' || name === 'mock') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if (/\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts')) out.push(full);
  }
  return out;
}

describe('no raw fetch', () => {
  test('the scan sees the source tree', () => {
    expect(sourceFiles(SRC).length).toBeGreaterThan(100);
  });

  test('every network call goes through guardedFetch', () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC)) {
      const rel = relative(SRC, file);
      if (ALLOWED.has(rel)) continue;
      readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
        const code = line.trimStart();
        if (code.startsWith('//') || code.startsWith('*') || code.startsWith('/*')) return;
        // `fetch` called (`fetch(`), passed as a value (`?? fetch;`, `= fetch,`,
        // `(fetch)`, `fetch.bind(`), or reached through the global object
        // (`window.fetch`, `window['fetch']`). Not `guardedFetch`, `prefetch`,
        // `refetch`, another object's `.fetch` method, or the word in prose.
        const call = /(?<![\w.$])fetch\s*\(/.test(line);
        const value =
          /(?:\?\?|\|\||&&|[=(,:?]|\breturn)\s*fetch\s*(?=[;,)\]}]|\?\?|\|\||$)/.test(line) ||
          /(?<![\w.$])fetch\s*\.\s*(?:bind|call|apply)\b/.test(line);
        const global = /\b(?:window|globalThis|self)\s*(?:\.\s*fetch\b|\[\s*['"`]fetch['"`]\s*\])/.test(line);
        if (call || value || global) {
          offenders.push(`${rel}:${i + 1}: ${code.slice(0, 80)}`);
        }
      });
    }
    expect(
      offenders,
      'raw fetch() bypasses the request-loop guard; use guardedFetch (lib/guardedFetch.ts) ' +
        'or add an explicit exemption with a reason',
    ).toEqual([]);
  });
});
