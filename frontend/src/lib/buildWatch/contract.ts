/**
 * @file_name: contract.ts (lib/buildWatch)
 * @date: 2026-11-02
 * @description: The /build.json contract shared by the build (vite.config.ts) and the tab (./index.ts).
 *
 * One file name, one field, written by the build and read by every open tab.
 * Both sides import it from here so renaming either cannot silently break the
 * "new version" banner. Pure: no DOM or Node APIs (vite.config.ts compiles
 * under tsconfig.node, ./index.ts under tsconfig.app).
 */
import type { Plugin } from 'vite';

/** Emitted at the root of dist/, served next to index.html. */
export const BUILD_JSON_FILE = 'build.json';

/** The build id in a /build.json body, or null if the body is not one. */
export function readBuildId(body: unknown): string | null {
  const id = (body as { buildId?: unknown } | null)?.buildId;
  return typeof id === 'string' && id ? id : null;
}

/** Vite plugin: emit /build.json carrying `buildId` (production builds only). */
export function emitBuildIdPlugin(buildId: string): Plugin {
  return {
    name: 'narranexus-emit-build-id',
    apply: 'build',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: BUILD_JSON_FILE, source: JSON.stringify({ buildId }) });
    },
  };
}
