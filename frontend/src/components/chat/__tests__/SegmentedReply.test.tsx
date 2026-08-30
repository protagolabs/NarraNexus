/**
 * SegmentedReply — one turn that spoke m times renders as m bubbles, each
 * carrying the process that led to it. The process is hidden while streaming
 * (it is in the ProcessPanel then, and painting it twice would duplicate it).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SegmentedReply } from '../SegmentedReply';
import type { Segment } from '@/types';
import { useUIStore } from '@/stores/uiStore';

/** Reasoning + tools, NO narration — the shape a claude/codex driver produces. */
const segments: Segment[] = [
  {
    process: [{ id: 't1', ts: 1, type: 'thinking', content: 'check the material' }],
    reply: { content: 'starting' },
  },
  {
    process: [
      { id: 'c2', ts: 2, type: 'tool_call', tool_name: 'bash', tool_input: {} },
      { id: 'o2', ts: 3, type: 'tool_output', tool_name: 'bash', output: 'ok' },
    ],
    reply: { content: 'done' },
  },
];

beforeEach(() => useUIStore.setState({ interimNarration: true }));

describe('SegmentedReply', () => {
  it('renders m bubbles for m replies', () => {
    render(<SegmentedReply segments={segments} />);
    expect(screen.getByText('starting')).toBeInTheDocument();
    expect(screen.getByText('done')).toBeInTheDocument();
  });

  it('preference off: each segment gets one drawer, labelled with its process count', () => {
    // Since 2026-08-30 the process is promoted into the message flow by
    // default; this drawer shape is what the preference restores.
    useUIStore.setState({ interimNarration: false });
    render(<SegmentedReply segments={segments} showProcess />);
    const entries = screen.getAllByTestId(/segment-details-/);
    expect(entries).toHaveLength(2);
    expect(entries[0]).toHaveTextContent('(1)');
    expect(entries[1]).toHaveTextContent('(2)');
  });

  it('preference on + the turn narrates: process is inline, with no outer drawer', () => {
    const narrated: Segment[] = [{
      process: [{ id: 't1', ts: 1, type: 'thinking', content: 'check the material', monologue: true }],
      reply: { content: 'starting' },
    }];
    render(<SegmentedReply segments={narrated} showProcess />);
    // The segment container is still there (the process renders inside it),
    // but there is no "Reasoning & tools (N)" master toggle — which is the
    // whole point: readable without clicking anything.
    expect(screen.getAllByTestId(/segment-details-/)).toHaveLength(1);
    expect(screen.queryByText(/\(1\)/)).toBeNull();
    expect(screen.getByText('check the material')).toBeInTheDocument();
  });

  it('preference on but the turn never narrates: drawer kept (claude/codex emit no monologue)', () => {
    // Promoting such a turn buys nothing and costs a different transcript
    // shape — from a setting named "Progress narration". No narration, no
    // promotion.
    render(<SegmentedReply segments={segments} showProcess />);
    expect(screen.getByText(/\(1\)/)).toBeInTheDocument();
    expect(screen.queryByText('check the material')).toBeNull();
  });

  it('promotion is decided per TURN, not per segment', () => {
    // A turn whose first segment narrates and whose second does not must not
    // render half inline and half behind a drawer — two shapes in one reply
    // reads as a rendering bug.
    const mixed: Segment[] = [
      {
        process: [{ id: 't1', ts: 1, type: 'thinking', content: 'narrating here', monologue: true }],
        reply: { content: 'first' },
      },
      {
        process: [{ id: 't2', ts: 2, type: 'thinking', content: 'reasoning only' }],
        reply: { content: 'second' },
      },
    ];
    render(<SegmentedReply segments={mixed} showProcess />);
    // Neither segment shows the drawer's count label.
    expect(screen.queryByText(/\(1\)/)).toBeNull();
    expect(screen.getByText('narrating here')).toBeInTheDocument();
  });

  it('no process while streaming — it lives in the ProcessPanel then', () => {
    render(<SegmentedReply segments={segments} isStreaming />);
    expect(screen.queryByTestId(/segment-details-/)).toBeNull();
    expect(screen.getByText('done')).toBeInTheDocument();
  });

  it('a segment with no reply renders no bubble, but keeps its process', () => {
    const noReply: Segment[] = [
      { process: [{ id: 't1', ts: 1, type: 'thinking', content: 'thought about it' }], reply: null },
    ];
    render(<SegmentedReply segments={noReply} showProcess />);
    expect(screen.queryByTestId('segment-reply-0')).toBeNull();
    expect(screen.getByTestId('segment-details-0')).toBeInTheDocument();
  });

  // The helper_llm recovery badges used to live on TurnTimeline's ReplyBlock;
  // they moved here with the answer tier (legacy tag kept — persisted rows
  // from before the rename are not backfilled).
  it('helper_llm_no_reply shows the info badge', () => {
    render(<SegmentedReply segments={[
      { process: [], reply: { content: 'Recovered reply', via: 'helper_llm_no_reply' } },
    ]} />);
    expect(screen.getByText(/helper_llm fallback/i)).toBeInTheDocument();
  });

  it('helper_llm_after_error shows the warning badge and not the info one', () => {
    render(<SegmentedReply segments={[
      { process: [], reply: { content: 'Partial recovery', via: 'helper_llm_after_error' } },
    ]} />);
    expect(screen.getByText(/recovered after error/i)).toBeInTheDocument();
    expect(screen.queryByText(/helper_llm fallback/i)).toBeNull();
  });

  it('the legacy helper_llm_fallback tag still shows the info badge', () => {
    render(<SegmentedReply segments={[
      { process: [], reply: { content: 'Legacy recovered reply', via: 'helper_llm_fallback' } },
    ]} />);
    expect(screen.getByText(/helper_llm fallback/i)).toBeInTheDocument();
  });
});

describe('SegmentedReply streaming render path', () => {
  // Re-parsing the whole markdown per delta saturates the main thread and
  // the UI visibly stalls, then the finished reply pops in at once (the
  // exact catch ThinkingBlock and the old ReplyBlock already documented).
  // While a segment streams, render plain pre-wrap text; markdown only on
  // settle.
  it('the streaming last segment is plain text (markdown not parsed)', () => {
    const segs: Segment[] = [
      { process: [], reply: { content: '**bold** text', streaming: true } },
    ];
    render(<SegmentedReply segments={segs} isStreaming />);
    // Literal asterisks visible = markdown NOT parsed.
    expect(screen.getByText('**bold** text')).toBeInTheDocument();
  });

  it('the same content goes through markdown once settled', () => {
    const segs: Segment[] = [
      { process: [], reply: { content: '**bold** text' } },
    ];
    render(<SegmentedReply segments={segs} />);
    expect(screen.queryByText('**bold** text')).toBeNull();
    expect(screen.getByText('bold')).toBeInTheDocument();
  });
});

describe('SegmentedReply defaultOpen', () => {
  // History path: the user already clicked "View reasoning" once to trigger
  // the fetch — landing on ANOTHER collapsed toggle would make it two clicks
  // to see anything, and N clicks for a verbose model.
  it('preference off + defaultOpen: the reasoning is readable with no further click', () => {
    useUIStore.setState({ interimNarration: false });
    render(<SegmentedReply segments={segments} showProcess defaultOpen />);
    expect(screen.getByText(/check the material/)).toBeInTheDocument();
  });

  it('preference off + defaultOpen: it can still be collapsed by hand', () => {
    useUIStore.setState({ interimNarration: false });
    render(<SegmentedReply segments={segments} showProcess defaultOpen />);
    fireEvent.click(screen.getAllByRole('button')[0]);
    expect(screen.queryByText(/check the material/)).toBeNull();
  });
});
