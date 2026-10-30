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
import { dirname, join, relative, resolve } from 'node:path';

const SRC = join(process.cwd(), 'src');
const MOCK_INDEX = join(SRC, 'lib/mock');
const MOCK_FIXTURES = join(SRC, 'lib/mock/fixtures');

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '__tests__' || name === 'dist') continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if (/\.(ts|tsx|js|jsx|mjs)$/.test(name) && !/\.d\.ts$|\.test\./.test(name)) out.push(full);
  }
  return out;
}

// Every static module specifier: `import … from`, `export … from` and the
// side-effect `import '…'`, anywhere on a line. Type-only forms are erased at
// build time and skipped; dynamic `import(…)` is the intended way in.
const SPECIFIER = /\b(import|export)\s+(type\s+)?(?:[^'";]*?\bfrom\s*)?['"]([^'"]+)['"]/g;

/** Resolve a specifier to an extension-less, index-less absolute path (null for packages). */
function resolveSpecifier(file: string, spec: string): string | null {
  let target: string;
  if (spec.startsWith('@/')) target = join(SRC, spec.slice(2));
  else if (spec.startsWith('.')) target = resolve(dirname(file), spec);
  else return null;
  return target.replace(/\/+$/, '').replace(/\.(ts|tsx|js|jsx|mjs)$/, '').replace(/\/index$/, '');
}

function mockImplImports(file: string, text: string): string[] {
  const hits: string[] = [];
  for (const m of text.matchAll(SPECIFIER)) {
    if (m[2]) continue; // import type / export type
    const target = resolveSpecifier(file, m[3]);
    if (target !== MOCK_INDEX && target !== MOCK_FIXTURES) continue;
    // The one allowed edge: the mock implementation importing its own fixtures.
    if (file === join(SRC, 'lib/mock/index.ts') && target === MOCK_FIXTURES) continue;
    hits.push(`${relative(SRC, file)}: ${m[3]}`);
  }
  return hits;
}

describe('mock layer is lazy', () => {
  test('no source file imports the mock implementation statically', () => {
    const offenders = sourceFiles(SRC).flatMap((f) => mockImplImports(f, readFileSync(f, 'utf8')));
    expect(offenders).toEqual([]);
  });

  test('the scan catches every static spelling, and lets type-only and dynamic imports through', () => {
    const api = join(SRC, 'lib/api.ts');
    const caught = [
      "import { mockApi } from './mock';",
      "import { mockApi } from '@/lib/mock/index.ts';",
      "import { x } from '@/lib/mock/fixtures.ts';",
      "export * from './mock';",
      "export { mockApi } from './mock/';",
      "import './mock/index.js';",
      "import type { MockApi } from './mock'; import { mockApi } from './mock';",
    ];
    for (const line of caught) expect(mockImplImports(api, line), line).not.toEqual([]);
    const allowed = [
      "import type { MockApi } from './mock';",
      "export type { MockApi } from './mock';",
      "import { MOCK_ENABLED } from './mock/flag';",
      "const m = await import('./mock');",
      "import { mock } from 'some-package/mock';",
    ];
    for (const line of allowed) expect(mockImplImports(api, line), line).toEqual([]);
    // flag.ts is scanned too: it is the eager half and must never pull the rest in.
    expect(mockImplImports(join(SRC, 'lib/mock/flag.ts'), "import { mockApi } from './index';")).not.toEqual([]);
  });

  test('main.tsx loads it with a dynamic import', () => {
    const main = readFileSync(join(SRC, 'main.tsx'), 'utf8');
    expect(main).toMatch(/import\(\s*['"]\.\/lib\/mock['"]\s*\)/);
  });

  test("the build gate's marker string still identifies the fixtures", () => {
    // check_bundle_budget.mjs fails a build whose first-load files contain any
    // budget.json initial_forbidden_strings entry; renaming the fixture value
    // would silently disarm it.
    const budget = JSON.parse(readFileSync(join(process.cwd(), 'budget.json'), 'utf8')) as { initial_forbidden_strings: string[] };
    const fixtures = readFileSync(`${MOCK_FIXTURES}.ts`, 'utf8');
    expect(budget.initial_forbidden_strings.length).toBeGreaterThan(0);
    for (const marker of budget.initial_forbidden_strings) expect(fixtures).toContain(marker);
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
