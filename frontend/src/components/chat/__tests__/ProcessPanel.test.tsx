/**
 * ProcessPanel — the live pipeline strip above the composer.
 *
 * Since 2026-08-30 it carries what the message flow does NOT: the pipeline
 * phases and the plan. The process events themselves (narration, tool lines,
 * reasoning) render in the flow through TurnTimeline, so drawing them here
 * too would paint the same rows twice — and burying the narration among
 * reasoning rows in a side panel is what made it invisible in the first place.
 *
 * Still pinned: phases render by real semantics, the whitelist holds, the plan
 * stays pinned at the bottom, collapse shows one activity line, and an empty
 * panel still renders the starting state.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ProcessPanel } from '../ProcessPanel';
import type { TurnEvent, Step } from '@/types';

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

function step(id: string, status: Step['status'] = 'running'): Step {
  return {
    id: `s-${id}`, step: id, title: `Step ${id}`, description: '',
    status, substeps: [], timestamp: 0,
  };
}

describe('ProcessPanel', () => {
  it('does not draw the process rows — those live in the message flow', () => {
    // The anti-duplication invariant. If these come back, the same narration
    // and tool lines paint twice: once in the flow, once in this panel.
    render(<ProcessPanel events={events} />);
    expect(screen.queryByText(/正在读取需求/)).toBeNull();
    expect(screen.queryByTestId('tool-row-c1')).toBeNull();
    expect(screen.queryByTestId('tool-row-c2')).toBeNull();
    // And it never drew the answer tier either.
    expect(screen.queryByText(/这句话属于气泡/)).toBeNull();
  });

  it('still summarises what is happening now in the collapsed activity line', () => {
    // The panel keeps reading the events even though it no longer lists them:
    // the header line is how a collapsed panel says what the agent is doing.
    render(<ProcessPanel events={events} />);
    fireEvent.click(screen.getByTestId('process-panel-header'));
    expect(screen.getByTestId('process-activity')).toHaveTextContent('register_artifact');
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

  // v3: the panel replaces the message-area "starting up…" indicator, so
  // it must render (with the starting status) even before any data.
  it('空数据渲染启动态而不是消失', () => {
    render(<ProcessPanel events={[]} />);
    expect(screen.getByTestId('process-panel')).toBeInTheDocument();
    expect(screen.getByText(/Starting up/)).toBeInTheDocument();
  });

  // Phase names must match what the backend actually does at each step:
  // step 1 = narrative selection (not "loading context"), step 3 = context
  // build, step 3.4 = the model actually running (the honest "entered the
  // agent loop" marker). See processShared PHASE_LABEL_KEYS.
  it('pipeline 阶段行按真实语义命名（选择叙事/加载模块/同步实例/构建上下文/运行 Agent）', () => {
    render(
      <ProcessPanel
        events={[]}
        steps={[
          step('1', 'completed'), step('2', 'completed'),
          step('2.5', 'completed'), step('3'), step('3.4'),
        ]}
      />,
    );
    expect(screen.getByText(/Selecting narrative/)).toBeInTheDocument();
    expect(screen.getByText(/Loading modules/)).toBeInTheDocument();
    expect(screen.getByText(/Syncing instances/)).toBeInTheDocument();
    expect(screen.getByText(/Building context/)).toBeInTheDocument();
    expect(screen.getByText(/Running agent/)).toBeInTheDocument();
    // The old mislabels — step 1 "Loading context", step 2 "Loading
    // resources" — are gone.
    expect(screen.queryByText(/Loading context/)).toBeNull();
    expect(screen.queryByText(/Loading resources/)).toBeNull();
  });

  // Only the whitelisted top-level phases render as rows. Tool sub-steps
  // (3.4.x), the 3.5 final-thinking echo, and post-answer housekeeping
  // (4 persist / 5 hooks) are NOT "what's happening now" phases — leaking
  // their raw English backend titles into the panel is the bug this guards.
  it('只渲染白名单阶段：工具子步(3.4.x)与收尾步骤(3.5/4/5)不进阶段行', () => {
    render(
      <ProcessPanel
        events={events}
        steps={[
          step('3'), step('3.4'), step('3.4.1'),
          step('3.5', 'completed'), step('4'), step('5'),
        ]}
      />,
    );
    expect(screen.queryByText('Step 3.4.1')).toBeNull();
    expect(screen.queryByText('Step 3.5')).toBeNull();
    expect(screen.queryByText('Step 4')).toBeNull();
    expect(screen.queryByText('Step 5')).toBeNull();
    // The real run-agent phase still shows, localized — never the raw title.
    expect(screen.getByText(/Running agent/)).toBeInTheDocument();
    expect(screen.queryByText('Step 3.4')).toBeNull();
  });

  describe('折叠', () => {
    it('点击头部折叠：阶段行隐藏，显示当前活动行', () => {
      render(<ProcessPanel events={events} steps={[step('3.4')]} />);
      fireEvent.click(screen.getByTestId('process-panel-header'));
      expect(screen.queryByText(/Running agent/)).toBeNull();
      // …one activity line: the latest pending tool is what's happening now.
      const activity = screen.getByTestId('process-activity');
      expect(activity).toHaveTextContent('register_artifact');
    });

    it('折叠态有 plan 时显示进展行，无 plan 时不显示', () => {
      render(<ProcessPanel events={events} />);
      fireEvent.click(screen.getByTestId('process-panel-header'));
      expect(screen.getByTestId('process-plan-mini')).toHaveTextContent('1/3');
      expect(screen.getByTestId('process-plan-mini')).toHaveTextContent('写正文');
    });

    it('折叠态无 plan：进展行不渲染', () => {
      const noPlan = events.filter((e) => e.type !== 'plan');
      render(<ProcessPanel events={noPlan} />);
      fireEvent.click(screen.getByTestId('process-panel-header'));
      expect(screen.queryByTestId('process-plan-mini')).toBeNull();
    });

    it('再点一次展开回来', () => {
      render(<ProcessPanel events={events} steps={[step('3.4')]} />);
      fireEvent.click(screen.getByTestId('process-panel-header'));
      fireEvent.click(screen.getByTestId('process-panel-header'));
      expect(screen.getByText(/Running agent/)).toBeInTheDocument();
    });
  });
});
