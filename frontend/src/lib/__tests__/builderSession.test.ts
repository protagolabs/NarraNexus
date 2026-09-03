/**
 * Consume-once is the contract worth pinning: a leaking mark would re-send
 * the whole builder instruction on every subsequent turn.
 */
import { describe, test, expect, beforeEach } from 'vitest';
import {
  clearBuilderPending,
  markBuilderPending,
  takeBuilderPending,
} from '../builderSession';

beforeEach(() => {
  window.sessionStorage.clear();
});

describe('builder pending mark', () => {
  test('an unmarked agent is not pending', () => {
    expect(takeBuilderPending('agt_1')).toBe(false);
  });

  test('a marked agent is pending exactly once', () => {
    markBuilderPending('agt_1');
    expect(takeBuilderPending('agt_1')).toBe(true);
    expect(takeBuilderPending('agt_1')).toBe(false);
  });

  test('marks are per agent', () => {
    markBuilderPending('agt_1');
    expect(takeBuilderPending('agt_2')).toBe(false);
    expect(takeBuilderPending('agt_1')).toBe(true);
  });

  test('clear drops the mark without reporting a send', () => {
    markBuilderPending('agt_1');
    clearBuilderPending('agt_1');
    expect(takeBuilderPending('agt_1')).toBe(false);
  });

  test('a blank agent id is inert in every direction', () => {
    markBuilderPending('');
    expect(takeBuilderPending('')).toBe(false);
    expect(() => clearBuilderPending('')).not.toThrow();
    expect(window.sessionStorage.length).toBe(0);
  });
});
