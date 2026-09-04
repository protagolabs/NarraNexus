/**
 * @file_name: vitePreset.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Build settings for a plugin bundle: one ESM file, host libraries external and resolved from `window.__narranexus_host__`.
 *
 * Returned as plain data (no vite import) so it can be consumed by any
 * bundler and unit-tested here. `HOST_EXTERNALS` maps the bare specifiers a
 * plugin imports to the property on the host global; the generated banner
 * shims those imports at runtime.
 */
export const HOST_EXTERNALS: Record<string, string> = {
  react: 'react',
  'react-dom': 'reactDom',
  'react-router-dom': 'router',
  zustand: 'zustand',
  i18next: 'i18next',
  'lucide-react': 'icons',
};

export interface VitePresetOptions {
  entry?: string;
  outDir?: string;
  fileName?: string;
}

export interface VitePresetResult {
  build: {
    lib: { entry: string; formats: ['es']; fileName: () => string };
    outDir: string;
    emptyOutDir: boolean;
    rollupOptions: { external: string[]; output: { inlineDynamicImports: boolean; paths: Record<string, string> } };
    sourcemap: boolean;
    minify: boolean;
  };
  define: Record<string, string>;
}

/** Runtime import shim: `import React from 'react'` → `window.__narranexus_host__.react`. */
export function hostShimModule(specifier: string): string {
  const prop = HOST_EXTERNALS[specifier];
  if (!prop) throw new Error(`no host shim for ${specifier}`);
  return `const m = globalThis.__narranexus_host__.${prop}; export default m; export const __esModule = true;`;
}

export function vitePreset(options: VitePresetOptions = {}): VitePresetResult {
  const fileName = options.fileName ?? 'plugin.js';
  return {
    build: {
      lib: { entry: options.entry ?? 'src/index.ts', formats: ['es'], fileName: () => fileName },
      outDir: options.outDir ?? 'dist',
      emptyOutDir: true,
      rollupOptions: {
        external: Object.keys(HOST_EXTERNALS),
        output: {
          inlineDynamicImports: true,
          paths: Object.fromEntries(Object.keys(HOST_EXTERNALS).map((k) => [k, `narranexus-host:${k}`])),
        },
      },
      sourcemap: false,
      minify: true,
    },
    define: { 'process.env.NODE_ENV': '"production"' },
  };
}
