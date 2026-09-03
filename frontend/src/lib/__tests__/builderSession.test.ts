/**
 * The flag must be READ-ONLY on read: the instruction rides along on every
 * turn, so a read that consumed the flag would silently stop the draft block
 * after the first message.
 */
import { describe, test, expect, beforeEach } from 'vitest';
import { closeStudio, isStudioOpen, openStudio } from '../builderSession';

beforeEach(() => {
  window.sessionStorage.clear();
});

describe('studio flag', () => {
  test('an untouched agent is not in the studio', () => {
    expect(isStudioOpen('agt_1')).toBe(false);
  });

  test('reading does not consume — every turn must still be wrapped', () => {
    openStudio('agt_1');
    expect(isStudioOpen('agt_1')).toBe(true);
    expect(isStudioOpen('agt_1')).toBe(true);
    expect(isStudioOpen('agt_1')).toBe(true);
  });

  test('close clears it', () => {
    openStudio('agt_1');
    closeStudio('agt_1');
    expect(isStudioOpen('agt_1')).toBe(false);
  });

  test('the flag is per agent', () => {
    openStudio('agt_1');
    expect(isStudioOpen('agt_2')).toBe(false);
  });

  test('closing one agent does not close another', () => {
    openStudio('agt_1');
    openStudio('agt_2');
    closeStudio('agt_1');
    expect(isStudioOpen('agt_1')).toBe(false);
    expect(isStudioOpen('agt_2')).toBe(true);
  });

  test('a blank or absent agent id is inert in every direction', () => {
    openStudio('');
    expect(isStudioOpen('')).toBe(false);
    expect(isStudioOpen(null)).toBe(false);
    expect(isStudioOpen(undefined)).toBe(false);
    expect(() => closeStudio('')).not.toThrow();
    expect(window.sessionStorage.length).toBe(0);
  });
});
