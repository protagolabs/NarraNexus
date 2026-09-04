/**
 * @file_name: sdk.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: definePlugin validates its shape; the vite preset externalises exactly the host libraries; shims resolve to the host global.
 */
import { describe, expect, it } from 'vitest';

import { HOST_EXTERNALS, definePlugin, hostShimModule, vitePreset } from '../index';

describe('@narranexus/sdk', () => {
  it('definePlugin requires activate', () => {
    expect(() => definePlugin({} as never)).toThrow(/activate/);
    const p = definePlugin({ activate() {} });
    expect(typeof p.activate).toBe('function');
  });

  it('vite preset produces a single ESM bundle with host externals', () => {
    const cfg = vitePreset({ entry: 'src/main.ts' });
    expect(cfg.build.lib.entry).toBe('src/main.ts');
    expect(cfg.build.lib.fileName()).toBe('plugin.js');
    expect(cfg.build.rollupOptions.external.sort()).toEqual(Object.keys(HOST_EXTERNALS).sort());
    expect(cfg.build.rollupOptions.output.inlineDynamicImports).toBe(true);
    expect(hostShimModule('react')).toContain('__narranexus_host__.react');
    expect(() => hostShimModule('lodash')).toThrow(/no host shim/);
  });
});
