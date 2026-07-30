/**
 * SegmentedReply — 一轮多次回复渲染成多个气泡；过程详情按段分隔，
 * 且直播期间不显示（那时过程在 ProcessPanel 里）。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SegmentedReply } from '../SegmentedReply';
import type { Segment } from '@/types';

const segments: Segment[] = [
  {
    process: [{ id: 't1', ts: 1, type: 'thinking', content: '先看素材' }],
    reply: { content: '开始了' },
  },
  {
    process: [
      { id: 'c2', ts: 2, type: 'tool_call', tool_name: 'bash', tool_input: {} },
      { id: 'o2', ts: 3, type: 'tool_output', tool_name: 'bash', output: 'ok' },
    ],
    reply: { content: '做完了' },
  },
];

describe('SegmentedReply', () => {
  it('m 段回复渲染成 m 个气泡', () => {
    render(<SegmentedReply segments={segments} />);
    expect(screen.getByText('开始了')).toBeInTheDocument();
    expect(screen.getByText('做完了')).toBeInTheDocument();
  });

  it('showProcess 时每段各有一个折叠入口，数量标注各自的过程条数', () => {
    render(<SegmentedReply segments={segments} showProcess />);
    const entries = screen.getAllByTestId(/segment-details-/);
    expect(entries).toHaveLength(2);
    expect(entries[0]).toHaveTextContent('(1)');
    expect(entries[1]).toHaveTextContent('(2)');
  });

  it('直播期间不显示过程——那时它在 ProcessPanel 里，两处都画就重复了', () => {
    render(<SegmentedReply segments={segments} isStreaming />);
    expect(screen.queryByTestId(/segment-details-/)).toBeNull();
    expect(screen.getByText('做完了')).toBeInTheDocument();
  });

  it('无回复的段不渲染气泡，但过程入口仍在', () => {
    const noReply: Segment[] = [
      { process: [{ id: 't1', ts: 1, type: 'thinking', content: '想了想' }], reply: null },
    ];
    render(<SegmentedReply segments={noReply} showProcess />);
    expect(screen.queryByTestId('segment-reply-0')).toBeNull();
    expect(screen.getByTestId('segment-details-0')).toBeInTheDocument();
  });

  // helper_llm 恢复徽标原先在 TurnTimeline 的 ReplyBlock 上；答案层迁到
  // 这里后徽标跟着走（含 legacy tag 兼容——改名前的持久化行不 backfill）。
  it('helper_llm_no_reply 显示 info 徽标', () => {
    render(<SegmentedReply segments={[
      { process: [], reply: { content: 'Recovered reply', via: 'helper_llm_no_reply' } },
    ]} />);
    expect(screen.getByText(/helper_llm fallback/i)).toBeInTheDocument();
  });

  it('helper_llm_after_error 显示 warning 徽标，且不显示 info 徽标', () => {
    render(<SegmentedReply segments={[
      { process: [], reply: { content: 'Partial recovery', via: 'helper_llm_after_error' } },
    ]} />);
    expect(screen.getByText(/recovered after error/i)).toBeInTheDocument();
    expect(screen.queryByText(/helper_llm fallback/i)).toBeNull();
  });

  it('legacy helper_llm_fallback tag 仍显示 info 徽标', () => {
    render(<SegmentedReply segments={[
      { process: [], reply: { content: 'Legacy recovered reply', via: 'helper_llm_fallback' } },
    ]} />);
    expect(screen.getByText(/helper_llm fallback/i)).toBeInTheDocument();
  });
});
