/**
 * @file_name: buildIdContract.test.ts
 * @description: What the build emits is exactly what an open tab reads back.
 */
import { describe, expect, it, vi } from 'vitest';

import { BUILD_JSON_FILE, emitBuildIdPlugin, readBuildId } from '../buildIdContract';

describe('build id contract', () => {
  it('the emitted /build.json round-trips through readBuildId', () => {
    const emitFile = vi.fn();
    const plugin = emitBuildIdPlugin('1.2.3+abc');
    expect(plugin.apply).toBe('build');
    (plugin.generateBundle as unknown as (this: { emitFile: typeof emitFile }) => void).call({ emitFile });
    expect(emitFile).toHaveBeenCalledTimes(1);
    const asset = emitFile.mock.calls[0][0];
    expect(asset).toMatchObject({ type: 'asset', fileName: BUILD_JSON_FILE });
    expect(readBuildId(JSON.parse(asset.source))).toBe('1.2.3+abc');
  });

  it('readBuildId rejects anything that is not a non-empty string id', () => {
    for (const body of [null, {}, { buildId: '' }, { buildId: 7 }, 'x', []]) {
      expect(readBuildId(body)).toBeNull();
    }
  });
});
