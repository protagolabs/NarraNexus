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

  it('a listener that reports from inside a report does not recurse', () => {
    let calls = 0;
    onUiError(() => {
      calls += 1;
      if (calls < 5) reportUiError(new Error('nested'));
    });
    reportUiError(new Error('outer'));
    expect(calls).toBe(1);
    expect(recentUiErrors().map((r) => r.error.message)).toEqual(['outer', 'nested']);
  });

  it('recentUiErrors returns a snapshot, not the live buffer', () => {
    reportUiError(new Error('one'));
    const snapshot = recentUiErrors();
    reportUiError(new Error('two'));
    expect(snapshot).toHaveLength(1);
    expect(recentUiErrors()).toHaveLength(2);
  });

  it('a throwing listener does not mask the report', () => {
    onUiError(() => {
      throw new Error('listener broke');
    });
    expect(() => reportUiError(new Error('x'))).not.toThrow();
  });
});
