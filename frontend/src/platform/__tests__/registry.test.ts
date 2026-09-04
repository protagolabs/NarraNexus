/**
 * @file_name: registry.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Registry semantics — order, conflicts, replace, dispose, freeze, subscriptions, snapshots.
 */
import { describe, expect, it, vi } from 'vitest';

import { Registry, RegistryConflictError } from '@/platform/registries/registry';

describe('Registry', () => {
  it('keeps registration order and exposes ids/values', () => {
    const r = new Registry<number>('demo');
    r.register('b', 2);
    r.register('a', 1, { owner: 'acme.x' });
    expect(r.ids()).toEqual(['b', 'a']);
    expect(r.get('a')).toBe(1);
    expect(r.list().map((e) => e.owner)).toEqual(['builtin.ui', 'acme.x']);
    expect(r.has('nope')).toBe(false);
  });

  it('rejects duplicates unless replace is passed', () => {
    const r = new Registry<number>('demo');
    r.register('a', 1);
    expect(() => r.register('a', 2)).toThrow(RegistryConflictError);
    r.register('a', 3, { replace: true });
    expect(r.get('a')).toBe(3);
  });

  it('dispose removes only its own registration', () => {
    const r = new Registry<number>('demo');
    const dispose = r.register('a', 1);
    r.register('a', 2, { replace: true });
    dispose();
    expect(r.get('a')).toBe(2);
    const d2 = r.register('b', 1);
    d2();
    expect(r.has('b')).toBe(false);
  });

  it('freeze closes registration', () => {
    const r = new Registry<number>('demo');
    r.freeze();
    expect(() => r.register('a', 1)).toThrow(/registration is closed/);
  });

  it('notifies subscribers and keeps snapshots referentially stable between changes', () => {
    const r = new Registry<number>('demo');
    const listener = vi.fn();
    const unsubscribe = r.subscribe(listener);
    const before = r.snapshot();
    expect(r.snapshot()).toBe(before);
    r.register('a', 1);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(r.snapshot()).not.toBe(before);
    unsubscribe();
    r.register('b', 2);
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe('Registry.removeOwner', () => {
  it('drops every entry of that owner, keeps the rest, and notifies once', () => {
    const r = new Registry<number>('demo');
    const listener = vi.fn();
    r.register('shell', 0);
    r.register('t1', 1, { owner: 'builtin.teams' });
    r.register('t2', 2, { owner: 'builtin.teams' });
    r.subscribe(listener);
    expect(r.removeOwner('builtin.teams')).toEqual(['t1', 't2']);
    expect(r.ids()).toEqual(['shell']);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(r.removeOwner('builtin.teams')).toEqual([]);
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
