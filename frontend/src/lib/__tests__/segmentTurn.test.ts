/**
 * segmentTurn — 一轮事件按「用户可见片段」切段。
 * 这些用例锁住四件事：多回复各带自己的过程、末尾残留归最后一段、
 * 零回复不丢过程、连续 native_output 合并。
 */
import { describe, it, expect } from 'vitest';
import { segmentTurn } from '../segmentTurn';
import type { TurnEvent } from '@/types';

const think = (id: string, content: string): TurnEvent =>
  ({ id, ts: 0, type: 'thinking', content });
const tool = (id: string, tool_name: string): TurnEvent =>
  ({ id, ts: 0, type: 'tool_call', tool_name, tool_input: {} });
const reply = (id: string, content: string): TurnEvent =>
  ({ id, ts: 0, type: 'reply', content });
const native = (id: string, content: string): TurnEvent =>
  ({ id, ts: 0, type: 'native_output', content });

describe('segmentTurn', () => {
  it('每段回复带上它之前的过程', () => {
    const segs = segmentTurn([
      think('t1', '先看素材'), tool('c1', 'glob'), reply('r1', '开始了'),
      think('t2', '排版'), tool('c2', 'bash'), reply('r2', '做完了'),
    ]);
    expect(segs).toHaveLength(2);
    expect(segs[0].reply?.content).toBe('开始了');
    expect(segs[0].process.map((e) => e.id)).toEqual(['t1', 'c1']);
    expect(segs[1].reply?.content).toBe('做完了');
    expect(segs[1].process.map((e) => e.id)).toEqual(['t2', 'c2']);
  });

  it('最后一次回复之后的过程归到最后一段', () => {
    const segs = segmentTurn([
      reply('r1', '说完了'), think('t9', '再检查一下'), tool('c9', 'read_file'),
    ]);
    expect(segs).toHaveLength(1);
    expect(segs[0].process.map((e) => e.id)).toEqual(['t9', 'c9']);
  });

  it('零回复的轮次产出一个无回复段，过程不丢', () => {
    const segs = segmentTurn([think('t1', '想了想'), tool('c1', 'bash')]);
    expect(segs).toHaveLength(1);
    expect(segs[0].reply).toBeNull();
    expect(segs[0].process.map((e) => e.id)).toEqual(['t1', 'c1']);
  });

  it('连续 native_output 合并成一段，被工具打断则开新段', () => {
    const segs = segmentTurn([
      native('n1', '第一句。'), native('n2', '接着说。'),
      tool('c1', 'bash'), native('n3', '换个话题。'),
    ]);
    expect(segs).toHaveLength(2);
    expect(segs[0].reply?.content).toBe('第一句。接着说。');
    expect(segs[1].reply?.content).toBe('换个话题。');
    expect(segs[1].process.map((e) => e.id)).toEqual(['c1']);
  });

  it('空输入产出空数组', () => {
    expect(segmentTurn([])).toEqual([]);
  });
});
