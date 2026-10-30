/**
 * @file_name: mockLazy.test.ts
 * @author: Bin Liang
 * @date: 2026-10-30
 * @description: The mock API layer stays out of the production entry chunk and still works when ?mock=1 loads it.
 *
 * The fixtures (lib/mock/index.ts + fixtures.ts) used to be imported by api.ts,
 * so every production page load shipped them behind a runtime flag. They are
 * now loaded by main.tsx on demand; one static import anywhere would silently
 * pull them back into the entry chunk, which only a source scan can catch.
 */
import { afterEach, describe, expect, test, vi } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(process.cwd(), 'src');

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '__tests__' || name === 'dist') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if (/\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts')) out.push(full);
  }
  return out;
}

// A value import of the mock implementation: `@/lib/mock`, `./mock`,
// `../lib/mock`, `./lib/mock/index`, `./fixtures` from inside lib/mock …
// `import type` is erased at build time and does not count; `lib/mock/flag`
// is the tiny switch that is meant to be eager.
const MOCK_IMPL = /(^|\/)(lib\/)?mock(\/(index|fixtures))?$/;
const STATIC_IMPORT = /^\s*import\s+(?!type\b)[^'"]*?from\s+['"]([^'"]+)['"]/gm;

describe('mock layer is lazy', () => {
  test('no source file imports the mock implementation statically', () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC)) {
      const rel = relative(SRC, file);
      if (rel.startsWith('lib/mock/')) continue; // index.ts importing its own fixtures
      for (const m of readFileSync(file, 'utf8').matchAll(STATIC_IMPORT)) {
        if (MOCK_IMPL.test(m[1])) offenders.push(`${rel}: ${m[1]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  test('main.tsx loads it with a dynamic import', () => {
    const main = readFileSync(join(SRC, 'main.tsx'), 'utf8');
    expect(main).toMatch(/import\(\s*['"]\.\/lib\/mock['"]\s*\)/);
  });
});

describe('installMockApi', () => {
  afterEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  test('in mock mode, installed methods answer and the rest fall through to the real client', async () => {
    localStorage.setItem('narranexus.mock.enabled', '1');
    vi.resetModules();
    const { api, installMockApi } = await import('../api');
    const { mockApi } = await import('../mock');
    const realGetJobs = api.getJobs;
    installMockApi(mockApi);
    const res = await api.getJobs('agent_x');
    expect(res.success).toBe(true);
    expect(api.getJobs).not.toBe(realGetJobs);
    // A method the mock does not define is the real client's.
    expect(typeof (api as unknown as Record<string, unknown>).request).toBe('function');
  });

  test('outside mock mode, api is the real client and installing does nothing', async () => {
    vi.resetModules();
    const { api, installMockApi } = await import('../api');
    const { mockApi } = await import('../mock');
    const before = api.getJobs;
    installMockApi(mockApi);
    expect(api.getJobs).toBe(before);
  });
});
