/**
 * Contract tests for the creation studio's first-message envelope.
 *
 * The strip test is the load-bearing one: the instruction rides inside a
 * USER message, so a strip regression puts the whole prompt in the user's
 * own chat bubble.
 */
import { describe, test, expect } from 'vitest';
import {
  AGENT_BUILDER_INSTRUCTION,
  BUILDER_MARK_CLOSE,
  BUILDER_MARK_OPEN,
  DRAFT_ARTIFACT_FILE,
  DRAFT_ARTIFACT_TITLE,
  buildBuilderFirstMessage,
  isConfigDraft,
  stripBuilderInstruction,
} from '../builderPrompt';
import type { Artifact } from '@/types/artifact';

describe('buildBuilderFirstMessage', () => {
  test('carries the user request verbatim after the instruction block', () => {
    const msg = buildBuilderFirstMessage('每天早上给我一份金融晨报');
    expect(msg).not.toBeNull();
    expect(msg).toContain(BUILDER_MARK_OPEN);
    expect(msg).toContain(BUILDER_MARK_CLOSE);
    expect(msg!.endsWith('每天早上给我一份金融晨报')).toBe(true);
  });

  test('trims the request', () => {
    const msg = buildBuilderFirstMessage('  盯竞品  ');
    expect(msg!.endsWith('盯竞品')).toBe(true);
  });

  test('refuses an empty request rather than sending instruction-only', () => {
    expect(buildBuilderFirstMessage('')).toBeNull();
    expect(buildBuilderFirstMessage('   \n  ')).toBeNull();
  });

  test('the instruction pins the artifact convention the apply bar keys off', () => {
    expect(AGENT_BUILDER_INSTRUCTION).toContain(DRAFT_ARTIFACT_FILE);
    expect(AGENT_BUILDER_INSTRUCTION).toContain(`title="${DRAFT_ARTIFACT_TITLE}"`);
    expect(AGENT_BUILDER_INSTRUCTION).toContain('target_artifact_id');
  });

  test('the instruction forbids claiming a config change and leaking secrets', () => {
    expect(AGENT_BUILDER_INSTRUCTION).toMatch(/DRAFT ONLY/);
    expect(AGENT_BUILDER_INSTRUCTION).toMatch(/never say you have/);
    expect(AGENT_BUILDER_INSTRUCTION).toMatch(/secrets, tokens, passwords/);
  });
});

describe('stripBuilderInstruction', () => {
  test('leaves only what the user typed', () => {
    const msg = buildBuilderFirstMessage('帮我做个周报助手')!;
    expect(stripBuilderInstruction(msg)).toBe('帮我做个周报助手');
  });

  test('returns ordinary messages untouched', () => {
    expect(stripBuilderInstruction('普通消息')).toBe('普通消息');
    expect(stripBuilderInstruction('')).toBe('');
  });

  test('drops an unterminated block instead of leaking the prompt', () => {
    const half = `${BUILDER_MARK_OPEN}\nYou are acting as the NarraNexus`;
    expect(stripBuilderInstruction(half)).toBe('');
  });

  test('handles more than one block', () => {
    const doubled = [
      BUILDER_MARK_OPEN, 'a', BUILDER_MARK_CLOSE,
      BUILDER_MARK_OPEN, 'b', BUILDER_MARK_CLOSE,
      'the request',
    ].join('\n');
    expect(stripBuilderInstruction(doubled)).toBe('the request');
  });

  test('does not eat a bracketed phrase that only looks like a marker', () => {
    const near = '[NARRANEXUS_AGENT_BUILDER] 这不是标记';
    expect(stripBuilderInstruction(near)).toBe(near);
  });
});

describe('isConfigDraft', () => {
  const draft = (over: Partial<Artifact>): Artifact =>
    ({ kind: 'text/markdown', title: DRAFT_ARTIFACT_TITLE, ...over }) as Artifact;

  test('recognises the artifact the instruction names', () => {
    expect(isConfigDraft(draft({}))).toBe(true);
  });

  test('tolerates surrounding whitespace in the title', () => {
    expect(isConfigDraft(draft({ title: ` ${DRAFT_ARTIFACT_TITLE} ` }))).toBe(true);
  });

  test('rejects other markdown artifacts — no apply button on unrelated docs', () => {
    expect(isConfigDraft(draft({ title: 'research-notes' }))).toBe(false);
    expect(isConfigDraft(draft({ title: 'agent-config-v2' }))).toBe(false);
  });

  test('rejects the right title with the wrong kind', () => {
    expect(isConfigDraft(draft({ kind: 'text/html' }))).toBe(false);
  });

  test('is safe on no artifact at all', () => {
    expect(isConfigDraft(null)).toBe(false);
    expect(isConfigDraft(undefined)).toBe(false);
  });
});
