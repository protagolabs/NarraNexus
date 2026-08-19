/**
 * @file_name: officeEditCommands.test.ts
 * @description: The T1 office edit vocabulary (spec B §3.3), as verified
 * against officecli 1.0.144 on 2026-08-19: batch items are
 * {command, path, props?} and the watch page reports selections as
 * {paths: [...]} — these builders/parsers are the single place that
 * encodes both facts.
 */

import { describe, expect, it } from 'vitest';
import {
  buildRemoveCommands,
  buildSetPropsCommands,
  buildSetTextCommand,
  parseSelectionMessage,
} from '../officeEditCommands';

describe('command builders', () => {
  it('remove: one command per selected path', () => {
    expect(buildRemoveCommands(['/slide[1]/pic[1]', '/slide[1]/sp[2]'])).toEqual([
      { command: 'remove', path: '/slide[1]/pic[1]' },
      { command: 'remove', path: '/slide[1]/sp[2]' },
    ]);
  });

  it('set props: bold/color per path', () => {
    expect(buildSetPropsCommands(['/body/p[1]'], { bold: true, color: 'FF0000' })).toEqual([
      { command: 'set', path: '/body/p[1]', props: { bold: true, color: 'FF0000' } },
    ]);
  });

  it('set text targets exactly one path', () => {
    expect(buildSetTextCommand('/Sheet1/row[2]/c[3]', 'new value')).toEqual({
      command: 'set',
      path: '/Sheet1/row[2]/c[3]',
      props: { text: 'new value' },
    });
  });
});

describe('parseSelectionMessage', () => {
  it('parses the watch page selection body', () => {
    expect(parseSelectionMessage('{"paths":["/body/p[1]","/body/p[2]"]}')).toEqual([
      '/body/p[1]',
      '/body/p[2]',
    ]);
  });

  it('tolerates a bare array body', () => {
    expect(parseSelectionMessage('["/body/p[1]"]')).toEqual(['/body/p[1]']);
  });

  it('returns empty on garbage', () => {
    expect(parseSelectionMessage('not json')).toEqual([]);
    expect(parseSelectionMessage('{"other":1}')).toEqual([]);
    expect(parseSelectionMessage('{"paths":"nope"}')).toEqual([]);
  });
});
