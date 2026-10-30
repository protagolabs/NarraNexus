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
      const body = readFileSync(file, 'utf8');
      for (const { index, text } of networkEgress(body)) {
        const lineStart = body.lastIndexOf('\n', index - 1) + 1;
        const line = body.slice(lineStart, body.indexOf('\n', index) === -1 ? undefined : body.indexOf('\n', index));
        const code = line.trimStart();
        if (code.startsWith('//') || code.startsWith('*') || code.startsWith('/*')) continue;
        const lineNo = body.slice(0, index).split('\n').length;
        offenders.push(`${rel}:${lineNo}: ${text.replace(/\s+/g, ' ').slice(0, 80)}`);
      }
    }
    expect(
      offenders,
      'raw network calls bypass the request-loop guard; use guardedFetch (lib/guardedFetch.ts) ' +
        'or add an explicit exemption with a reason',
    ).toEqual([]);
  });

  test('the scan catches the shapes it claims to', () => {
    const hits = (src: string) => networkEgress(src).length;
    expect(hits('const r = await fetch(url);')).toBe(1);
    expect(hits('const f = deps.fetchImpl ??\n  fetch;')).toBe(1); // split across lines
    expect(hits('const f = fetch.bind(window);')).toBe(1);
    expect(hits("window['fetch'](u); globalThis.fetch(u);")).toBe(2);
    expect(hits('new XMLHttpRequest(); new EventSource(u); navigator.sendBeacon(u, b);')).toBe(3);
    expect(hits('await guardedFetch(url); refetch(); prefetch(x); query.fetch(); const { fetchStatus } = a;')).toBe(0);
    expect(hits("setError('Failed to fetch agents'); // fetch in prose")).toBe(0);
  });
});

/**
 * Every place in `body` that reaches the network without guardedFetch: `fetch`
 * called (`fetch(`), passed as a value (`?? fetch`, `= fetch`, `(fetch)`,
 * `fetch.bind`), or reached through the global object (`window.fetch`,
 * `window['fetch']`), plus the other egress APIs (XMLHttpRequest, EventSource,
 * sendBeacon). Matched over the whole file so a call split across lines is
 * still seen; callers skip matches that sit on comment lines.
 */
function networkEgress(body: string): { index: number; text: string }[] {
  const patterns = [
    /(?<![\w.$])fetch\s*\(/g,
    /(?:\?\?|\|\||&&|[=(,:?]|\breturn)\s*fetch\s*(?=[;,)\]}]|\?\?|\|\||$)/gm,
    /(?<![\w.$])fetch\s*\.\s*(?:bind|call|apply)\b/g,
    /\b(?:window|globalThis|self)\s*(?:\.\s*fetch\b|\[\s*['"`]fetch['"`]\s*\])/g,
    /\bnew\s+(?:XMLHttpRequest|EventSource)\b/g,
    /\bsendBeacon\s*\(/g,
  ];
  const found = new Map<number, string>();
  for (const re of patterns) {
    for (const m of body.matchAll(re)) found.set(m.index ?? 0, m[0]);
  }
  return [...found.entries()].sort((x, y) => x[0] - y[0]).map(([index, text]) => ({ index, text }));
}
