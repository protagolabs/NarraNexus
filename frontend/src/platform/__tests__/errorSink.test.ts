/**
 * @file_name: errorSink.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: UI errors are attributed to the shell or a plugin and delivered to subscribers.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { attributeChunkUrl, onUiError, recentUiErrors, reportUiError, resetErrorSink } from '@/platform/errorSink';

afterEach(() => resetErrorSink());

describe('error sink', () => {
  it('attributes by chunk url and notifies listeners', () => {
    attributeChunkUrl('plugin://acme.weather/', 'acme.weather');
    const seen = vi.fn();
    onUiError(seen);
    const err = new Error('boom');
    err.stack = 'Error: boom\n    at render (plugin://acme.weather/plugin.js:1:1)';
    const report = reportUiError(err, { kind: 'render' });
    expect(report.source).toBe('acme.weather');
    expect(seen).toHaveBeenCalledWith(report);
    expect(reportUiError(new Error('shell')).source).toBe('shell');
    expect(recentUiErrors()).toHaveLength(2);
  });

  it('a throwing listener does not mask the report', () => {
    onUiError(() => {
      throw new Error('listener broke');
    });
    expect(() => reportUiError(new Error('x'))).not.toThrow();
  });
});
