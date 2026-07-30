/**
 * ProcessPanel — 运行中的过程面板。测四件事：只渲染过程事件、
 * plan 固定在底部、pending 工具有进行中标记、无过程时不渲染。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProcessPanel } from '../ProcessPanel';
import type { TurnEvent } from '@/types';

const events: TurnEvent[] = [
  { id: 't1', ts: 1, type: 'thinking', content: '正在读取需求' },
  { id: 'c1', ts: 2, type: 'tool_call', tool_name: 'glob', tool_input: { pattern: '**/*.md' } },
  { id: 'c2', ts: 3, type: 'tool_call', tool_name: 'register_artifact', tool_input: {}, pending: true },
  { id: 'r1', ts: 4, type: 'reply', content: '这句话属于气泡，不该出现在面板里' },
  { id: 'p1', ts: 5, type: 'plan', steps: [
      { step: '建目录', status: 'completed' },
      { step: '写正文', status: 'in_progress' },
      { step: '注册', status: 'pending' },
    ] },
];

describe('ProcessPanel', () => {
  it('渲染思考与工具，不渲染回复', () => {
    render(<ProcessPanel events={events} />);
    expect(screen.getByText(/正在读取需求/)).toBeInTheDocument();
    expect(screen.getByText('glob')).toBeInTheDocument();
    expect(screen.queryByText(/这句话属于气泡/)).toBeNull();
  });

  it('pending 工具显示名字并标记进行中', () => {
    render(<ProcessPanel events={events} />);
    const row = screen.getByTestId('tool-row-c2');
    expect(row).toHaveTextContent('register_artifact');
    expect(row).toHaveAttribute('data-pending', 'true');
  });

  it('参数齐了的工具不带进行中标记', () => {
    render(<ProcessPanel events={events} />);
    expect(screen.getByTestId('tool-row-c1')).toHaveAttribute('data-pending', 'false');
  });

  it('plan 渲染在底部固定区，含三个步骤', () => {
    render(<ProcessPanel events={events} />);
    const plan = screen.getByTestId('process-plan');
    expect(plan).toHaveTextContent('建目录');
    expect(plan).toHaveTextContent('写正文');
    expect(plan).toHaveTextContent('注册');
  });

  it('plan 多次更新时只渲染最后一份快照', () => {
    const withTwoPlans: TurnEvent[] = [
      ...events,
      { id: 'p2', ts: 6, type: 'plan', steps: [{ step: '收尾', status: 'in_progress' }] },
    ];
    render(<ProcessPanel events={withTwoPlans} />);
    const plan = screen.getByTestId('process-plan');
    expect(plan).toHaveTextContent('收尾');
    expect(plan).not.toHaveTextContent('建目录');
  });

  it('没有过程事件也没有 plan 时不渲染任何东西', () => {
    const { container } = render(<ProcessPanel events={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
